"""
Скан рынка: все USDT linear perpetual, фильтры ликвидности, кэш OHLCV, параллельная загрузка.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from typing import Any

from app.config import get_settings
from app.data.market_data import MarketData
from app.exchange.bybit_client import BybitClient

logger = logging.getLogger(__name__)

# (symbol, timeframe, limit) -> (ts_monotonic, rows)
_OHLCV_CACHE: dict[tuple[str, str, int], tuple[float, list[list[Any]]]] = {}

_CLIENT = BybitClient()
_MD = MarketData(_CLIENT)


def clear_ohlcv_cache() -> None:
    _OHLCV_CACHE.clear()


def fetch_all_symbols() -> list[str]:
    """Список всех linear USDT perpetual (учитывает SCAN_ALL_* из resolve_scan_symbols)."""
    s = get_settings()
    return s.resolve_scan_symbols()


def _fetch_ohlcv_raw(symbol: str, timeframe: str, limit: int) -> list[list[Any]]:
    # BybitClient already applies retry/backoff + error normalization.
    return _CLIENT.fetch_ohlcv(symbol, timeframe, limit)


def _timeframe_to_ms(timeframe: str) -> int:
    t = (timeframe or "5m").strip().lower()
    try:
        if t.endswith("m"):
            return max(60_000, int(float(t[:-1]) * 60_000))
        if t.endswith("h"):
            return max(3_600_000, int(float(t[:-1]) * 3_600_000))
    except (TypeError, ValueError):
        pass
    return 300_000


def build_mock_ohlcv(symbol: str, limit: int, timeframe: str = "5m") -> list[list[Any]]:
    """Синтетические OHLCV для paper, если биржа недоступна (детерминированный шум по символу)."""
    step_ms = _timeframe_to_ms(timeframe)
    now_ms = int(time.time() * 1000)
    seed = abs(hash(symbol)) % (2**31)
    rng = random.Random(seed)
    base = 50.0 + (seed % 7000) / 100.0
    price = base
    rows: list[list[Any]] = []
    start = now_ms - (limit - 1) * step_ms
    for i in range(limit):
        ts = start + i * step_ms
        o = price
        c = price * (1.0 + rng.uniform(-0.004, 0.004))
        h = max(o, c) * (1.0 + abs(rng.uniform(0, 0.002)))
        l = min(o, c) * (1.0 - abs(rng.uniform(0, 0.002)))
        v = rng.uniform(800.0, 8000.0)
        rows.append([ts, o, h, l, c, v])
        price = c
    return rows


def fetch_ohlcv_cached(symbol: str, timeframe: str = "5m", limit: int = 280) -> list[list[Any]]:
    s = get_settings()
    ttl = float(s.ohlcv_cache_ttl_sec)
    key = (symbol, timeframe, limit)
    now = time.monotonic()
    ent = _OHLCV_CACHE.get(key)
    if ent is not None and now - ent[0] < ttl:
        return ent[1]
    rows = _fetch_ohlcv_raw(symbol, timeframe, limit)
    if (not rows) and bool(getattr(s, "mock_ohlcv_on_empty", False)):
        rows = build_mock_ohlcv(symbol, limit, timeframe)
        logger.warning(
            "mock_ohlcv_used symbol=%s timeframe=%s limit=%s",
            symbol,
            timeframe,
            limit,
        )
    _OHLCV_CACHE[key] = (now, rows)
    if len(_OHLCV_CACHE) > 5000:
        # мягкая уборка старых записей
        cutoff = now - ttl * 2
        for k in list(_OHLCV_CACHE.keys())[:2000]:
            if _OHLCV_CACHE[k][0] < cutoff:
                del _OHLCV_CACHE[k]
    return rows


def fetch_tickers_map() -> dict[str, dict[str, Any]]:
    """Все тикеры swap (для quoteVolume)."""
    return _MD.fetch_tickers()


def quote_volume_usdt(tickers: dict[str, dict[str, Any]], symbol: str) -> float:
    t = tickers.get(symbol) or {}
    for k in ("quoteVolume", "quoteVolume24h", "turnover24h", "baseVolume"):
        v = t.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def atr_band_risk_score(atr_pct: float, lo: float, hi: float) -> float:
    """
    0.35..1.0: выше у центра коридора ATR (слишком тихий/дикий рынок — ниже).
    """
    if hi <= lo:
        return 0.55
    mid = (lo + hi) / 2.0
    span = max(hi - lo, 1e-12)
    d = abs(float(atr_pct) - mid) / span
    return float(0.35 + 0.65 * max(0.0, 1.0 - min(1.0, d * 2.0)))


def compute_atr_soft_score(
    atr_pct: float,
    *,
    opt_lo: float,
    opt_hi: float,
    floor: float,
) -> float:
    """
    Мягкий множитель для скоринга: ~1.0 в «оптимальной» полосе (по умолчанию 0.5%–3%),
    плавный спад в тихий/дикий хвост, не ниже floor.
    """
    x = float(atr_pct)
    if not math.isfinite(x) or x <= 0:
        return float(floor)
    lo = float(opt_lo)
    hi = float(opt_hi)
    fl = float(floor)
    if lo > hi:
        lo, hi = hi, lo
    if lo <= x <= hi:
        return 1.0
    if x < lo:
        if lo <= 0:
            return fl
        ratio = min(1.0, x / lo)
        return fl + (1.0 - fl) * (ratio**0.55)
    over = x - hi
    width = max(0.04, 0.25 - hi)
    penalty = min(1.0, over / width)
    return max(fl, 1.0 - 0.92 * penalty)


def liquidity_score_from_quote_vol(quote_vol: float, *, ref: float = 5e6) -> float:
    """0..1, логарифмическая нормализация к ref USDT за 24h."""
    if quote_vol <= 0:
        return 0.05
    return float(min(1.0, math.log1p(quote_vol) / math.log1p(max(ref, 1.0))))


def filter_symbols(
    symbols: list[str],
    tickers: dict[str, dict[str, Any]],
    *,
    min_quote_volume_usdt: float,
) -> list[str]:
    if min_quote_volume_usdt <= 0:
        return list(symbols)
    out: list[str] = []
    for sym in symbols:
        qv = quote_volume_usdt(tickers, sym)
        if qv >= min_quote_volume_usdt:
            out.append(sym)
    if not out:
        logger.warning(
            "filter_symbols: после фильтра объёма не осталось пар (min=%s), возвращаю исходный список",
            min_quote_volume_usdt,
        )
        return list(symbols)
    return out


async def scan_symbols_parallel(
    symbols: list[str],
    *,
    timeframe: str = "5m",
    limit: int = 280,
    max_concurrency: int = 12,
) -> dict[str, list[list[Any]]]:
    """
    Параллельная загрузка OHLCV через asyncio.to_thread (ccxt синхронный).
    """
    sem = asyncio.Semaphore(max(1, max_concurrency))
    results: dict[str, list[list[Any]]] = {}
    errors: list[str] = []

    async def one(sym: str) -> None:
        async with sem:
            try:
                rows = await asyncio.to_thread(fetch_ohlcv_cached, sym, timeframe, limit)
                results[sym] = rows
            except Exception as e:
                errors.append(f"{sym}:{type(e).__name__}")
                results[sym] = []

    await asyncio.gather(*(one(s) for s in symbols))
    if errors:
        logger.debug("scan_parallel_errors sample: %s", errors[:8])
    return results
