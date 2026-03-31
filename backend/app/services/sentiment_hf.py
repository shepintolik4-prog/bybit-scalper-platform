"""
Sentiment через Hugging Face transformers (локальная модель).
Ленивая загрузка; без HF_TOKEN публичные модели часто доступны.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)
_pipeline: Any = None


def _get_pipeline() -> Any | None:
    global _pipeline
    if _pipeline is not None:
        return _pipeline if _pipeline else None
    s = get_settings()
    if not s.hf_sentiment_enabled:
        _pipeline = False
        return None
    try:
        from transformers import pipeline

        _pipeline = pipeline(
            "sentiment-analysis",
            model=s.hf_sentiment_model,
            tokenizer=s.hf_sentiment_model,
            device=-1,
        )
        return _pipeline
    except Exception as e:
        logger.warning("hf_sentiment_init_failed: %s", e)
        _pipeline = False
        return None


def score_text(text: str) -> float | None:
    """Возвращает скор в диапазоне примерно [-1, 1]: негатив → -1, позитив → 1."""
    if not text.strip():
        return None
    pipe = _get_pipeline()
    if not pipe:
        return None
    try:
        chunk = text[:2000]
        out = pipe(chunk[:512])[0]
        label = str(out.get("label", "")).lower()
        score = float(out.get("score", 0.5))
        if "pos" in label or label == "label_2":
            return score
        if "neg" in label or label == "label_0":
            return -score
        return 0.0
    except Exception as e:
        logger.debug("hf_sentiment_score_failed: %s", e)
        return None


def aggregate_scores(texts: list[str]) -> float | None:
    if not texts:
        return None
    vals = [score_text(t) for t in texts if t]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)
