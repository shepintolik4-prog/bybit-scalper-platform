"""
Сверка live-позиций БД с Bybit (ccxt): закрытие «фантомов», усыновление сирот,
исправление размера/цены входа. Источник истины для notional — биржа.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sentry_sdk
from sqlalchemy.orm import Session

from app.config import get_settings
from app.ml.predictor import explanation_to_json
from app.models.orm import BotSettings, Position, TradeRecord
from app.monitoring.prometheus_metrics import execution_sync_mismatch_total
from app.services import bybit_exchange
from app.services.execution_model import apply_execution_price
from app.services.trade_journal import append_trade_outcome
from app.services.strategy_performance import record_trade_closed

logger = logging.getLogger("scalper.exchange_sync")


@dataclass
class NormalizedExchangePosition:
    symbol: str
    side: str  # buy | sell
    contracts: float  # base size signed: long > 0, short < 0 in ccxt often absolute + side
    entry_price: float
    mark_price: float
    notional_usdt: float
    leverage: int
    initial_margin_usdt: float | None
    raw: dict[str, Any]


def _parse_side_contracts(p: dict[str, Any]) -> tuple[str, float]:
    """ccxt unified position: side long/short, contracts signed or unsigned."""
    side_raw = (p.get("side") or "").lower()
    contracts = float(p.get("contracts") or 0)
    if side_raw in ("short", "sell"):
        sd = "sell"
        c = abs(contracts)
    else:
        sd = "buy"
        c = abs(contracts)
    return sd, c


def fetch_positions_from_bybit() -> list[NormalizedExchangePosition]:
    ex = bybit_exchange.create_exchange()
    ex.load_markets()
    rows = ex.fetch_positions()
    out: list[NormalizedExchangePosition] = []
    for p in rows:
        sym = p.get("symbol")
        if not sym:
            continue
        contracts_abs = float(p.get("contracts") or 0)
        if abs(contracts_abs) < 1e-12:
            continue
        side, c = _parse_side_contracts(p)
        entry = float(p.get("entryPrice") or 0) or 0.0
        mark = float(p.get("markPrice") or entry or 0) or entry
        notional = float(p.get("notional") or 0)
        if notional <= 0 and entry > 0 and c > 0:
            try:
                m = ex.market(sym)
                ct_val = float(m.get("contractSize") or 1)
            except Exception:
                ct_val = 1.0
            notional = abs(c * ct_val * entry)
        lev = int(float(p.get("leverage") or 1))
        if lev < 1:
            lev = 1
        im = p.get("initialMargin")
        initial_margin = float(im) if im is not None else (notional / lev if lev else notional)
        out.append(
            NormalizedExchangePosition(
                symbol=sym,
                side=side,
                contracts=c,
                entry_price=entry,
                mark_price=mark,
                notional_usdt=abs(notional),
                leverage=lev,
                initial_margin_usdt=initial_margin,
                raw=dict(p),
            )
        )
    return out


def _ex_map(rows: list[NormalizedExchangePosition]) -> dict[str, NormalizedExchangePosition]:
    return {r.symbol: r for r in rows}


def fetch_position_marks_for_symbols(symbols: list[str]) -> dict[str, float]:
    """Один round-trip fetch_positions: mark price по открытым символам (live risk path)."""
    if not symbols:
        return {}
    want = set(symbols)
    try:
        rows = fetch_positions_from_bybit()
    except Exception:
        return {}
    return {r.symbol: float(r.mark_price) for r in rows if r.symbol in want and r.contracts >= 1e-8}


def _close_db_position_exchange(
    db: Session,
    st: BotSettings,
    pos: Position,
    *,
    reason: str,
    exit_price: float,
    expl_extra: dict[str, Any],
) -> None:
    """Закрыть позицию в БД по факту биржи (без рыночного ордера бота)."""
    frx = apply_execution_price(exit_price, "sell" if pos.side == "buy" else "buy")
    exit_fill = frx.fill_price
    pnl_pct = (
        (exit_fill - pos.entry_price) / max(pos.entry_price, 1e-12) * (1 if pos.side == "buy" else -1) * pos.leverage
    ) * 100
    pnl_usdt = pos.size_usdt * (pnl_pct / 100.0)
    fee_exit = max(pos.size_usdt * (get_settings().exec_fee_roundtrip_pct / 200.0), 0.0)
    if pos.mode == "paper":
        margin = pos.size_usdt
        st.virtual_balance += margin + pnl_usdt - fee_exit

    expl = json.loads(pos.explanation_json) if pos.explanation_json else {}
    expl["close"] = {
        "reason": reason,
        "exit_mid": exit_price,
        "exit_fill": exit_fill,
        "pnl_pct": round(pnl_pct, 4),
        "source": "exchange_sync",
        **expl_extra,
    }

    tr = (
        db.query(TradeRecord)
        .filter(
            TradeRecord.symbol == pos.symbol,
            TradeRecord.status == "open",
            TradeRecord.mode == pos.mode,
        )
        .order_by(TradeRecord.id.desc())
        .first()
    )
    if tr:
        tr.exit_price = exit_fill
        net_pnl = pnl_usdt - fee_exit
        tr.pnl_usdt = net_pnl
        tr.pnl_pct = pnl_pct
        tr.closed_at = datetime.utcnow()
        tr.status = "closed"
        tr.order_status = "filled"
        tr.data_source = "exchange"
        tr.explanation_json = explanation_to_json(expl)
        try:
            from app.monitoring.prometheus_metrics import trade_pnl_usdt, trades_closed

            trades_closed.labels(mode=pos.mode, reason=reason).inc()
            trade_pnl_usdt.observe(float(net_pnl))
        except Exception:
            pass
        try:
            append_trade_outcome(
                {
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "reason": reason,
                    "pnl_usdt": round(float(net_pnl), 6),
                    "ok": float(net_pnl) >= 0,
                    "mode": pos.mode,
                    "regime": expl.get("regime"),
                    "combined_edge_at_open": expl.get("combined_edge"),
                    "strategy_id": expl.get("strategy_id") or "unknown",
                }
            )
            record_trade_closed(
                str(expl.get("strategy_id") or "unknown"),
                float(net_pnl),
                float(net_pnl) >= 0,
            )
        except Exception:
            pass

    db.delete(pos)


def sync_positions_with_db(db: Session, st: BotSettings | None = None) -> dict[str, Any]:
    """
    Сверка только для mode=live. Paper не трогаем.
    Возвращает сводку для логов/метрик.
    """
    st = st or db.query(BotSettings).filter_by(id=1).first()
    if not st or st.paper_mode:
        return {"skipped": True, "reason": "paper_mode"}

    summary: dict[str, Any] = {
        "closed_phantom": 0,
        "adopted_orphan": 0,
        "size_fixed": 0,
        "price_fixed": 0,
        "errors": [],
    }

    try:
        ex_rows = fetch_positions_from_bybit()
    except Exception as e:
        logger.exception("fetch_positions_from_bybit: %s", e)
        try:
            sentry_sdk.capture_exception(e)
        except Exception:
            pass
        summary["errors"].append(str(e))
        return summary

    ex_by_sym = _ex_map(ex_rows)
    db_live = list(db.query(Position).filter(Position.mode == "live").all())

    for pos in db_live:
        ex = ex_by_sym.get(pos.symbol)
        if ex is None or ex.contracts < 1e-8:
            exit_px = float(pos.last_mark_price or pos.entry_price)
            _close_db_position_exchange(
                db,
                st,
                pos,
                reason="exchange_flat",
                exit_price=exit_px,
                expl_extra={"detail": "no_position_on_exchange"},
            )
            summary["closed_phantom"] += 1
            try:
                execution_sync_mismatch_total.labels(kind="phantom_db").inc()
            except Exception:
                pass
            continue

        pos.last_mark_price = ex.mark_price
        pos.last_exchange_sync_at = datetime.utcnow()
        pos.data_source = "exchange"

        margin_target = float(ex.initial_margin_usdt or (ex.notional_usdt / max(ex.leverage, 1)))
        if margin_target > 0 and abs(margin_target - pos.size_usdt) / max(pos.size_usdt, 1e-9) > 0.08:
            pos.size_usdt = margin_target
            summary["size_fixed"] += 1
            try:
                execution_sync_mismatch_total.labels(kind="size").inc()
            except Exception:
                pass

        if ex.entry_price > 0 and abs(ex.entry_price - pos.entry_price) / max(pos.entry_price, 1e-9) > 0.002:
            pos.entry_price = ex.entry_price
            summary["price_fixed"] += 1
            try:
                execution_sync_mismatch_total.labels(kind="entry").inc()
            except Exception:
                pass

        pos.contracts_qty = ex.contracts
        if ex.leverage and ex.leverage != pos.leverage:
            pos.leverage = ex.leverage

        tr = (
            db.query(TradeRecord)
            .filter(
                TradeRecord.symbol == pos.symbol,
                TradeRecord.status == "open",
                TradeRecord.mode == "live",
            )
            .order_by(TradeRecord.id.desc())
            .first()
        )
        if tr:
            tr.data_source = "exchange"

    db.flush()
    live_syms = {p.symbol for p in db.query(Position).filter(Position.mode == "live").all()}
    for sym, ex in ex_by_sym.items():
        if sym in live_syms:
            continue
        if ex.contracts < 1e-8:
            continue
        margin = float(ex.initial_margin_usdt or (ex.notional_usdt / max(ex.leverage, 1)))
        expl = {
            "strategy_id": "exchange_orphan",
            "regime": "unknown",
            "exchange_adopted": True,
            "adopted_at": time.time(),
            "raw_mark": ex.mark_price,
        }
        npos = Position(
            symbol=sym,
            side=ex.side,
            entry_price=ex.entry_price,
            size_usdt=margin,
            leverage=ex.leverage,
            stop_loss=ex.entry_price * (0.97 if ex.side == "buy" else 1.03),
            take_profit=ex.entry_price * (1.05 if ex.side == "buy" else 0.95),
            highest_price=ex.mark_price if ex.side == "buy" else None,
            lowest_price=ex.mark_price if ex.side == "sell" else None,
            trail_price=None,
            explanation_json=explanation_to_json(expl),
            mode="live",
            data_source="exchange",
            contracts_qty=ex.contracts,
            last_mark_price=ex.mark_price,
            last_exchange_sync_at=datetime.utcnow(),
        )
        db.add(npos)
        tr_or = TradeRecord(
            symbol=sym,
            side=ex.side,
            entry_price=ex.entry_price,
            size_usdt=margin,
            stop_loss=npos.stop_loss,
            take_profit=npos.take_profit,
            explanation_json=explanation_to_json(expl),
            mode="live",
            status="open",
            order_status="filled",
            data_source="exchange",
        )
        db.add(tr_or)
        live_syms.add(sym)
        summary["adopted_orphan"] += 1
        try:
            execution_sync_mismatch_total.labels(kind="orphan_adopted").inc()
        except Exception:
            pass

    db.commit()
    return summary


def attempt_reduce_only_market_close_with_retries(
    pos: Position,
    *,
    attempts: int = 3,
    delay_sec: float = 0.35,
) -> bool:
    """
    Повтор reduceOnly market close при временных ошибках API.
    Логирует исчерпание попыток; успех после ретрая — INFO.
    """
    n = max(1, int(attempts))
    for i in range(n):
        ok = attempt_reduce_only_market_close(pos)
        if ok:
            if i > 0:
                logger.info(
                    "attempt_reduce_only_market_close_retry_ok symbol=%s attempt=%s/%s",
                    pos.symbol,
                    i + 1,
                    n,
                )
            return True
        if i + 1 < n and delay_sec > 0:
            time.sleep(delay_sec)
    logger.warning(
        "attempt_reduce_only_market_close_exhausted symbol=%s attempts=%s",
        pos.symbol,
        n,
    )
    return False


def attempt_reduce_only_market_close(pos: Position) -> bool:
    """
    Рыночное закрытие с reduceOnly. True — ордер отправлен или позиции уже нет.
    False — ошибка API: не удалять позицию из БД в этом тике (повтор позже).
    """
    try:
        ex = bybit_exchange.create_exchange()
        ex.load_markets()
        rows = fetch_positions_from_bybit()
        match = next((r for r in rows if r.symbol == pos.symbol and r.contracts >= 1e-8), None)
        if match is None:
            return True
        amt = float(ex.amount_to_precision(pos.symbol, match.contracts))
        if amt <= 0:
            return True
        side_close = "sell" if pos.side == "buy" else "buy"
        link = f"x{uuid.uuid4().hex[:31]}"
        params: dict[str, Any] = {"reduceOnly": True, "orderLinkId": link}
        ex.create_order(pos.symbol, "market", side_close, amt, None, params)
        return True
    except Exception as e:
        logger.exception("attempt_reduce_only_market_close %s: %s", pos.symbol, e)
        try:
            sentry_sdk.capture_exception(e)
        except Exception:
            pass
        return False
