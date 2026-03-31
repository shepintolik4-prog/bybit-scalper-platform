"""Контекст тика: новости (Firecrawl) + агрегированный sentiment (HF)."""
from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.services.news_firecrawl import build_context_from_env
from app.services.sentiment_hf import aggregate_scores


def build_tick_context() -> dict[str, Any]:
    s = get_settings()
    out: dict[str, Any] = {"news_enabled": s.news_enabled, "sentiment": None, "news": {}, "texts": []}
    if not s.news_enabled:
        return out
    news = build_context_from_env()
    out["news"] = news
    out["texts"] = news.get("texts", [])
    if s.hf_sentiment_enabled and out["texts"]:
        out["sentiment"] = aggregate_scores(out["texts"])
    return out


def adjust_edge(combined: float, sentiment: float | None, s: Any) -> float:
    if sentiment is None or not s.hf_sentiment_enabled:
        return combined
    # sentiment in ~[-1,1] масштабируем в малый вклад в edge
    return combined + float(sentiment) * float(s.news_sentiment_scale)
