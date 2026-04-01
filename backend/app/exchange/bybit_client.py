from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import ccxt

from app.core.config import get_settings
from app.core.retry import RetryPolicy, retry_call
from app.exchange.errors import (
    ExchangeAuthError,
    ExchangeBadRequest,
    ExchangeError,
    ExchangeRateLimitError,
    ExchangeTemporaryError,
)
from app.services import bybit_exchange

logger = logging.getLogger("scalper.exchange.bybit_client")


@dataclass(frozen=True)
class ExchangeMeta:
    testnet: bool
    source: str


class BybitClient:
    """
    Robust wrapper around ccxt Bybit for TESTNET-first operation.

    На первом шаге остаёмся совместимыми с текущим `services/bybit_exchange.py`,
    но все внешние вызовы проходят через retry/backoff и нормализацию ошибок.
    """

    def __init__(self, *, policy: RetryPolicy | None = None) -> None:
        self._settings = get_settings()
        self._policy = policy or RetryPolicy()

    def create_trading_exchange(self) -> ccxt.bybit:
        return bybit_exchange.create_exchange()

    def create_public_exchange(self) -> ccxt.bybit:
        return bybit_exchange.create_public_exchange()

    def _is_retryable(self, e: Exception) -> bool:
        # ccxt transient categories
        if isinstance(
            e,
            (
                ccxt.NetworkError,
                ccxt.ExchangeNotAvailable,
                ccxt.RequestTimeout,
                ccxt.DDoSProtection,
                ccxt.RateLimitExceeded,
            ),
        ):
            return True
        msg = (str(e) or "").lower()
        # Bybit rate limit / transient gateway issues often surface as text
        if "10006" in msg or "too many requests" in msg:
            return True
        if "timeout" in msg or "timed out" in msg or "connection" in msg:
            return True
        if "bad gateway" in msg or "service unavailable" in msg:
            return True
        return False

    def _map_error(self, e: Exception) -> ExchangeError:
        # Map ccxt error types to domain-level exceptions
        if isinstance(e, ccxt.AuthenticationError):
            return ExchangeAuthError(str(e))
        if isinstance(e, ccxt.BadRequest):
            return ExchangeBadRequest(str(e))
        if isinstance(e, ccxt.RateLimitExceeded):
            return ExchangeRateLimitError(str(e))
        if self._is_retryable(e):
            return ExchangeTemporaryError(str(e))
        return ExchangeError(str(e))

    def _call(self, fn, *, op: str) -> Any:
        def _do():
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                mapped = self._map_error(e)
                if isinstance(mapped, ExchangeTemporaryError):
                    logger.warning("bybit transient error op=%s err=%s", op, mapped)
                raise mapped

        return retry_call(
            _do,
            policy=self._policy,
            should_retry=lambda e: isinstance(e, ExchangeTemporaryError),
        )

    def fetch_ohlcv(self, symbol: str, timeframe: str = "5m", limit: int = 200) -> list[list[Any]]:
        return self._call(
            lambda: bybit_exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit),
            op="fetch_ohlcv",
        )

    def fetch_mark_price_candidates(self, symbol: str, *, prefer_ticker: bool) -> list[tuple[float, float]]:
        return self._call(
            lambda: bybit_exchange.fetch_mark_price_candidates(symbol, prefer_ticker=prefer_ticker),
            op="fetch_mark_price_candidates",
        )

    def get_usdt_linear_symbols(self, *, refresh_sec: int = 3600, max_symbols: int = 0) -> list[str]:
        return self._call(
            lambda: bybit_exchange.get_usdt_linear_perpetual_symbols(
                refresh_sec=refresh_sec,
                max_symbols=max_symbols,
            ),
            op="get_usdt_linear_symbols",
        )


__all__ = ["BybitClient", "ExchangeMeta"]

