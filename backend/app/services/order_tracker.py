"""
Отслеживание статуса ордера на Bybit (ccxt): заполнение, частичное исполнение,
обновление TradeRecord и связанной Position (notional / contracts).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import sentry_sdk
from sqlalchemy.orm import Session

from app.config import get_settings
from app.ml.predictor import explanation_to_json
from app.models.orm import Position, TradeRecord
from app.monitoring.prometheus_metrics import (
    execution_order_partial_total,
    execution_orders_filled_total,
)
from app.services import bybit_exchange

logger = logging.getLogger("scalper.order_tracker")


def _map_ccxt_status(status: str | None, filled: float, amount: float | None) -> str:
    s = (status or "").lower()
    if s in ("closed", "filled"):
        return "filled"
    if s in ("open", "new", "pending", "untriggered"):
        if filled > 0 and amount and filled + 1e-12 < amount:
            return "partially_filled"
        return "pending"
    if s in ("canceled", "cancelled", "rejected", "expired"):
        return "canceled"
    if filled > 0 and amount and filled + 1e-12 < amount:
        return "partially_filled"
    if filled > 0:
        return "filled"
    return "unknown"


def track_order_status(
    db: Session,
    *,
    symbol: str,
    exchange_order_id: str | None,
    client_order_id: str | None,
    position_hint: Position | None = None,
    trade_hint: TradeRecord | None = None,
) -> dict[str, Any]:
    """
    Подтянуть ордер с биржи и обновить TradeRecord (+ опционально Position по symbol).
    """
    ex = bybit_exchange.create_exchange()
    ex.load_markets()
    order: dict[str, Any] | None = None
    err: str | None = None
    try:
        if exchange_order_id:
            order = ex.fetch_order(exchange_order_id, symbol)
        elif client_order_id:
            since = int((time.time() - 86400) * 1000)
            for o in ex.fetch_open_orders(symbol, since=since, limit=50):
                link = str(o.get("clientOrderId") or o.get("info", {}).get("orderLinkId") or "")
                if link == client_order_id:
                    order = o
                    break
            if order is None:
                for o in ex.fetch_closed_orders(symbol, since=since, limit=50):
                    link = str(o.get("clientOrderId") or o.get("info", {}).get("orderLinkId") or "")
                    if link == client_order_id:
                        order = o
                        break
    except Exception as e:
        err = str(e)
        logger.warning("track_order_status fetch failed %s %s: %s", symbol, exchange_order_id, e)
        try:
            sentry_sdk.capture_exception(e)
        except Exception:
            pass

    out: dict[str, Any] = {"ok": order is not None, "error": err, "order": order}

    if order is None:
        return out

    filled = float(order.get("filled") or 0)
    amount = order.get("amount")
    amount_f = float(amount) if amount is not None else filled
    status = _map_ccxt_status(order.get("status"), filled, amount_f)
    avg = order.get("average")
    avg_price = float(avg) if avg is not None else float(order.get("price") or 0)

    tr = trade_hint
    if tr is None and client_order_id:
        tr = db.query(TradeRecord).filter(TradeRecord.client_order_id == client_order_id).first()
    if tr is None and exchange_order_id:
        tr = db.query(TradeRecord).filter(TradeRecord.exchange_order_id == exchange_order_id).first()

    if tr:
        prev_status = tr.order_status
        oid = order.get("id")
        if oid:
            tr.exchange_order_id = str(oid)
        tr.order_status = status
        tr.filled_contracts = filled
        if client_order_id and not tr.client_order_id:
            tr.client_order_id = client_order_id
        if status == "partially_filled" and prev_status != "partially_filled":
            try:
                execution_order_partial_total.inc()
            except Exception:
                pass
        if status == "filled" and prev_status != "filled":
            try:
                execution_orders_filled_total.inc()
            except Exception:
                pass
        expl = json.loads(tr.explanation_json) if tr.explanation_json else {}
        expl["order_track"] = {
            "status": status,
            "filled": filled,
            "amount": amount_f,
            "avg_price": avg_price,
            "raw_status": order.get("status"),
        }
        tr.explanation_json = explanation_to_json(expl)

    pos = position_hint
    if pos is None:
        pos = (
            db.query(Position)
            .filter(Position.symbol == symbol, Position.mode == "live")
            .first()
        )

    if pos and tr and tr.status == "open" and filled > 0 and avg_price > 0:
        try:
            m = ex.market(symbol)
            ct = float(m.get("contractSize") or 1)
        except Exception:
            ct = 1.0
        notional = abs(filled * ct * avg_price)
        lev = max(int(pos.leverage), 1)
        margin = notional / lev
        pos.size_usdt = margin
        pos.contracts_qty = filled
        pos.entry_price = avg_price

    if tr:
        db.flush()

    return out


def update_trade_record(
    db: Session,
    tr: TradeRecord,
    *,
    order_status: str,
    exchange_order_id: str | None = None,
    filled_contracts: float | None = None,
    data_source: str = "exchange",
) -> None:
    tr.order_status = order_status
    tr.data_source = data_source
    if exchange_order_id:
        tr.exchange_order_id = exchange_order_id
    if filled_contracts is not None:
        tr.filled_contracts = filled_contracts
    db.flush()


def verify_position_opened_after_order(
    symbol: str,
    *,
    side: str,
    min_contracts: float = 1e-8,
    max_wait_sec: float | None = None,
    sleep_sec: float = 0.25,
) -> tuple[bool, Any]:
    """Проверить, что на бирже реально появилась позиция (failsafe)."""
    from app.services.exchange_sync import fetch_positions_from_bybit

    settings = get_settings()
    deadline = time.time() + float(max_wait_sec or settings.live_order_confirm_timeout_sec)
    while time.time() < deadline:
        try:
            rows = fetch_positions_from_bybit()
            for r in rows:
                if r.symbol != symbol:
                    continue
                if r.contracts < min_contracts:
                    continue
                if r.side != side:
                    continue
                return True, r
        except Exception as e:
            logger.warning("verify_position_opened_after_order: %s", e)
        time.sleep(sleep_sec)
    return False, None
