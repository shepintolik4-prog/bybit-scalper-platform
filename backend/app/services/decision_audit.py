"""Структурированная цепочка «почему так решили» для explanation_json."""
from __future__ import annotations

from typing import Any


def build_decision_chain(
    *,
    symbol: str,
    side: str,
    regime: str,
    combined_edge: float,
    need_edge: float,
    meta_ok: bool | None,
    meta_p: float | None,
    sentiment: float | None,
    news_titles: list[str],
    fund_ok: bool,
    adaptive_bias: float,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    steps.append({"step": 1, "rule": "regime", "detail": regime})
    steps.append(
        {
            "step": 2,
            "rule": "edge_vs_threshold",
            "combined_edge": round(combined_edge, 5),
            "need_edge": round(need_edge, 5),
            "adaptive_bias": round(adaptive_bias, 5),
        }
    )
    if meta_p is not None:
        steps.append({"step": 3, "rule": "meta_filter", "p_trade": round(meta_p, 4), "passed": meta_ok})
    if sentiment is not None:
        steps.append({"step": 4, "rule": "news_sentiment", "score": round(sentiment, 4)})
    if news_titles:
        steps.append({"step": 5, "rule": "news_sources", "titles": news_titles[:5]})
    steps.append({"step": 6, "rule": "fund_limits", "passed": fund_ok})
    return {
        "symbol": symbol,
        "side": side,
        "chain": steps,
        "summary": f"{side.upper()} {symbol}: edge {combined_edge:.4f} vs need {need_edge:.4f} (bias {adaptive_bias:+.4f})",
    }
