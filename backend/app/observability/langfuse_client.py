"""
Langfuse: трейсы решений бота (почему вошли / отказ).
Опционально: без ключей клиент не создаётся.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)
_client: Any = None


def get_langfuse() -> Any | None:
    global _client
    if _client is not None:
        return _client if _client else None
    s = get_settings()
    if not s.langfuse_enabled or not s.langfuse_public_key or not s.langfuse_secret_key:
        _client = False
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=s.langfuse_public_key,
            secret_key=s.langfuse_secret_key,
            host=s.langfuse_host or "https://cloud.langfuse.com",
        )
        return _client
    except Exception as e:
        logger.warning("langfuse_init_failed: %s", e)
        _client = False
        return None


def trace_trade_decision(
    *,
    symbol: str,
    side: str,
    explanation: dict[str, Any],
    paper: bool,
) -> None:
    lf = get_langfuse()
    if not lf:
        return
    try:
        import json

        payload = json.dumps(explanation, ensure_ascii=False, default=str)[:12000]
        span = lf.start_observation(
            name="trade_open",
            as_type="span",
            input={"symbol": symbol, "side": side, "paper": paper},
            output=payload,
            metadata={"source": "bybit-scalper"},
        )
        span.end()
        lf.flush()
    except Exception as e:
        logger.debug("langfuse_trace_failed: %s", e)


def trace_reject(symbol: str, reason: str, extra: dict[str, Any] | None = None) -> None:
    lf = get_langfuse()
    if not lf:
        return
    try:
        span = lf.start_observation(
            name="signal_reject",
            as_type="span",
            input={"symbol": symbol, "reason": reason, **(extra or {})},
            metadata={"type": "reject"},
        )
        span.end()
        lf.flush()
    except Exception:
        pass
