from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.data.cache import TTLCache
from app.exchange.bybit_client import BybitClient


class MarketData:
    """
    Unified data access layer (initially thin wrapper around ccxt via BybitClient).
    """

    def __init__(self, client: BybitClient | None = None) -> None:
        self._s = get_settings()
        self._client = client or BybitClient()
        self._ohlcv_cache: TTLCache[list[list[Any]]] = TTLCache(ttl_sec=float(self._s.ohlcv_cache_ttl_sec))
        self._tickers_cache: TTLCache[dict[str, dict[str, Any]]] = TTLCache(ttl_sec=5.0)
        self._orderbook_cache: TTLCache[dict[str, Any]] = TTLCache(
            ttl_sec=float(getattr(self._s, "orderbook_cache_ttl_sec", 1.2))
        )

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list[list[Any]]:
        key = f"ohlcv:{symbol}:{timeframe}:{limit}"
        return self._ohlcv_cache.get_or_set(key, lambda: self._client.fetch_ohlcv(symbol, timeframe, limit))

    def fetch_tickers(self) -> dict[str, dict[str, Any]]:
        def _load() -> dict[str, dict[str, Any]]:
            ex = self._client.create_public_exchange()
            ex.load_markets()
            tickers = ex.fetch_tickers()
            return tickers if isinstance(tickers, dict) else {}

        return self._tickers_cache.get_or_set("tickers", _load)

    def fetch_orderbook(self, symbol: str, *, limit: int = 50) -> dict[str, Any]:
        key = f"ob:{symbol}:{int(limit)}"

        def _load() -> dict[str, Any]:
            ex = self._client.create_public_exchange()
            ex.load_markets()
            ob = ex.fetch_order_book(symbol, limit=int(limit))
            return ob if isinstance(ob, dict) else {}

        return self._orderbook_cache.get_or_set(key, _load)

