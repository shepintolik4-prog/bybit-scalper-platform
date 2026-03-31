"""Расширенный health для внешнего watchdog (без секретов)."""
from __future__ import annotations

import time

from fastapi import APIRouter

from app.services.bot_engine import engine
from app.services.trading_control_store import load_control

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/watchdog")
def health_watchdog():
    ctrl = load_control()
    since_open = None
    if engine._last_open_ts is not None:
        since_open = time.time() - engine._last_open_ts
    return {
        "last_tick_seconds": round(float(engine._last_tick_seconds), 4),
        "seconds_since_last_trade_open": round(since_open, 3) if since_open is not None else None,
        "trading_paused": bool(ctrl.get("paused")),
        "trading_pause_reason": ctrl.get("reason") or "",
        "trading_pause_source": ctrl.get("source") or "",
    }
