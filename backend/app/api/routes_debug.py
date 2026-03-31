"""Диагностика: полная проверка окружения (нужен X-API-Secret)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import verify_api_secret
from app.services.startup_self_check import run_full_system_check

router = APIRouter(prefix="/debug/system", tags=["debug"])


@router.get("/full_check")
def full_system_check(_: None = Depends(verify_api_secret)) -> dict:
    """БД, публичный Bybit OHLCV, resolve_scan_symbols, снимок конфига (без секретов)."""
    return run_full_system_check()
