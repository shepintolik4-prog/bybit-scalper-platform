"""
Периодические проверки согласованности БД, позиций и баланса.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.orm import BotSettings, Position, TradeRecord
from app.monitoring.prometheus_metrics import consistency_errors_total
from app.services import bybit_exchange
from app.services.event_bus import E_CONSISTENCY_ERROR, emit
from app.services.exchange_sync import fetch_positions_from_bybit

logger = logging.getLogger("scalper.consistency")

_last_snapshot: dict[str, Any] = {}


def get_last_consistency_snapshot() -> dict[str, Any]:
    return dict(_last_snapshot)


def run_consistency_checks(db: Session, st: BotSettings) -> dict[str, Any]:
    """
    Возвращает сводку; инкрементирует Prometheus по каждой найденной проблеме.
    Для paper — только мягкие проверки (без приватного API).
    """
    summary: dict[str, Any] = {"paper": st.paper_mode, "issues": [], "ok": True}
    issues: list[str] = summary["issues"]
    s = get_settings()
    if not s.consistency_checks_enabled:
        return summary

    pos_mode = "paper" if st.paper_mode else "live"
    db_positions = db.query(Position).filter(Position.mode == pos_mode).all()

    # Открытая сделка без позиции / позиция без открытой сделки
    open_trades = (
        db.query(TradeRecord)
        .filter(TradeRecord.mode == pos_mode, TradeRecord.status == "open")
        .all()
    )
    syms_pos = {p.symbol for p in db_positions}
    syms_tr = {t.symbol for t in open_trades}
    for t in open_trades:
        if t.symbol not in syms_pos:
            msg = f"open_trade_no_position:{t.symbol}"
            issues.append(msg)
            _bump("trade_without_position")
    for p in db_positions:
        if p.symbol not in syms_tr:
            msg = f"position_no_open_trade:{p.symbol}"
            issues.append(msg)
            _bump("position_without_trade")

    if not st.paper_mode:
        try:
            eq = bybit_exchange.create_exchange()
            bal = eq.fetch_balance()
            usdt_total = float(bal.get("USDT", {}).get("total") or 0)
            summary["exchange_usdt_total"] = usdt_total
            if usdt_total <= 0 and len(db_positions) > 0:
                issues.append("exchange_zero_balance_with_db_positions")
                _bump("balance_margin_mismatch")
        except Exception as e:
            issues.append(f"balance_fetch:{e!s}")
            _bump("exchange_unreachable")

        try:
            ex_pos = fetch_positions_from_bybit()
            ex_syms = {r.symbol for r in ex_pos if r.contracts >= 1e-8}
            for p in db_positions:
                if p.symbol not in ex_syms:
                    issues.append(f"db_position_missing_on_exchange:{p.symbol}")
                    _bump("db_vs_exchange_position")
            for sym in ex_syms:
                if sym not in syms_pos:
                    issues.append(f"exchange_position_missing_in_db:{sym}")
                    _bump("exchange_vs_db_position")
        except Exception as e:
            issues.append(f"positions_fetch:{e!s}")
            _bump("exchange_unreachable")

    if issues:
        summary["ok"] = False
        emit(E_CONSISTENCY_ERROR, {"issues": issues[:20]})
        logger.warning("consistency issues: %s", issues[:10])
    global _last_snapshot
    _last_snapshot = summary
    return summary


def _bump(check: str) -> None:
    try:
        consistency_errors_total.labels(check=check).inc()
    except Exception:
        pass
