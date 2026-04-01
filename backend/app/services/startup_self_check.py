"""
Автодиагностика при старте и по GET /debug/system/full_check.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.database import engine

logger = logging.getLogger("scalper.self_check")


def run_full_system_check() -> dict[str, Any]:
    s = get_settings()
    out: dict[str, Any] = {"status": "READY", "checks": {}}

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        out["checks"]["database"] = {"ok": True, "url_host": _db_host_only(s.database_url)}
    except Exception as e:
        out["checks"]["database"] = {"ok": False, "error": str(e)}
        out["status"] = "ERROR"

    try:
        from app.exchange.bybit_client import BybitClient

        rows = BybitClient().fetch_ohlcv("BTC/USDT:USDT", "5m", 5)
        ok = bool(rows) and len(rows) >= 1
        out["checks"]["bybit_public_ohlcv"] = {"ok": ok, "bars": len(rows or [])}
        if not ok:
            out["status"] = "ERROR"
    except Exception as e:
        out["checks"]["bybit_public_ohlcv"] = {"ok": False, "error": str(e)}
        out["status"] = "ERROR"

    try:
        from app.services import market_scanner

        syms = market_scanner.fetch_all_symbols()
        out["checks"]["scan_symbols_resolve"] = {"ok": True, "count": len(syms)}
        if not syms:
            out["status"] = "ERROR"
            out["checks"]["scan_symbols_resolve"]["ok"] = False
    except Exception as e:
        out["checks"]["scan_symbols_resolve"] = {"ok": False, "error": str(e)}
        out["status"] = "ERROR"

    out["checks"]["config"] = {
        "debug": bool(getattr(s, "debug", False)),
        "scan_all_usdt_perpetual": s.scan_all_usdt_perpetual,
        "scan_all_max_symbols": s.scan_all_max_symbols,
        "scan_interval_sec": s.scan_interval_sec,
        "signal_min_edge": s.signal_min_edge,
        "min_model_confidence": s.min_model_confidence,
        "max_position_lifetime_sec": s.max_position_lifetime_sec,
        "mock_ohlcv_on_empty": bool(getattr(s, "mock_ohlcv_on_empty", False)),
        "paper_quiet_fallback_sec": float(getattr(s, "paper_quiet_fallback_sec", 0) or 0),
        "bybit_testnet": s.bybit_testnet,
        "api_secret_configured": bool(s.api_secret),
    }

    if out["status"] == "READY":
        logger.info("self_check READY %s", out["checks"].get("config"))
    else:
        logger.error("self_check ERROR %s", out["checks"])
    return out


def _db_host_only(url: str) -> str:
    try:
        if "@" in url:
            return url.split("@", 1)[-1].split("/")[0]
        return "?"
    except Exception:
        return "?"
