"""Статус автономного контура: adaptive bias, контекст новостей (без секретов)."""
from __future__ import annotations

import time

from fastapi import APIRouter

from app.config import get_settings
from app.services.adaptive_state import load_adaptive
from app.services.autonomy_context import build_tick_context
from app.services.bot_engine import engine as bot_engine

router = APIRouter(prefix="/api/autonomy", tags=["autonomy"])


@router.get("/status")
def autonomy_status():
    s = get_settings()
    state = load_adaptive()
    ctx = build_tick_context()
    news = ctx.get("news") or {}
    n_scan = len(s.resolve_scan_symbols())
    return {
        "scan_all_usdt_perpetual": s.scan_all_usdt_perpetual,
        "scan_symbols_count": n_scan,
        "news_enabled": s.news_enabled,
        "hf_sentiment_enabled": s.hf_sentiment_enabled,
        "self_improve_enabled": s.self_improve_enabled,
        "auto_retrain_enabled": s.auto_retrain_enabled,
        "langfuse_enabled": s.langfuse_enabled,
        "adaptive": {
            "edge_bias": state.get("edge_bias"),
            "last_winrate": state.get("last_winrate"),
            "last_n": state.get("last_n"),
        },
        "tick_context_preview": {
            "sentiment": ctx.get("sentiment"),
            "news_url_count": len(news.get("titles") or []),
        },
        "watchdog_hints": {
            "last_tick_seconds": round(float(bot_engine._last_tick_seconds), 4),
            "seconds_since_last_trade_open": (
                round(time.time() - bot_engine._last_open_ts, 3)
                if bot_engine._last_open_ts is not None
                else None
            ),
        },
    }
