"""Снимок сканера рынка для дашборда (топ сигналов, отклонения)."""
from __future__ import annotations

import time
from fastapi import APIRouter

from app.services.scan_state import get_snapshot
from app.services.bot_engine import engine as bot_engine

router = APIRouter(prefix="/api/scan", tags=["scan"])


@router.get("/snapshot")
def scan_snapshot():
    snap = get_snapshot()
    # Фолбэк: если воркер ещё не успел опубликовать снимок (или завис на долгом REST),
    # UI всё равно должен увидеть "что сканируем" и не выглядеть сломанным.
    if snap.get("updated_at") is None:
        syms = bot_engine._universe_symbols_for_dashboard(cache_ttl_sec=30.0)
        now = time.time()
        snap["updated_at"] = now
        snap["tick_epoch"] = now
        snap["scanned_symbols"] = syms
        snap["scanned_count"] = len(syms)
    return snap
