import logging
import time
from typing import Any

import ccxt

from app.config import get_settings
from app.database import SessionLocal
from app.services.key_manager import get_credential_source

logger = logging.getLogger(__name__)

_USDT_LINEAR_CACHE: list[str] | None = None
_USDT_LINEAR_CACHE_TS: float = 0.0


def create_exchange() -> ccxt.bybit:
    s = get_settings()
    db = SessionLocal()
    try:
        key, secret, testnet, _src = get_credential_source(db)
    finally:
        db.close()
    opts: dict[str, Any] = {
        "apiKey": key or None,
        "secret": secret or None,
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    }
    ex = ccxt.bybit(opts)
    if testnet:
        ex.set_sandbox_mode(True)
    return ex


def create_public_exchange() -> ccxt.bybit:
    """
    Клиент без API-ключей: публичные свечи/книги.
    Иначе неверные ключи в .env/БД ломают fetch_ohlcv (Bybit 10003).
    Флаг testnet берётся так же, как у create_exchange (из БД или .env).
    """
    db = SessionLocal()
    try:
        _key, _secret, testnet, _src = get_credential_source(db)
    finally:
        db.close()
    opts: dict[str, Any] = {
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    }
    ex = ccxt.bybit(opts)
    if testnet:
        ex.set_sandbox_mode(True)
    return ex


def fetch_ohlcv(symbol: str, timeframe: str = "5m", limit: int = 200) -> list[list[Any]]:
    ex = create_public_exchange()
    return ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)


def fetch_mark_price_candidates(symbol: str, *, prefer_ticker: bool) -> list[tuple[float, float]]:
    """
    Все доступные оценки last/mark: (цена, возраст_сек).
    Нужно для TP/SL: «самая свежая» цена может расходиться с 1m close — тогда шорт не закрывается по TP,
    а UI показывает mark из другого источника.
    """
    ex = create_public_exchange()
    now_ms = time.time() * 1000.0
    candidates: list[tuple[float, float]] = []

    if prefer_ticker:
        try:
            t = ex.fetch_ticker(symbol)
            raw = t.get("last") or t.get("close")
            if raw is None and isinstance(t.get("info"), dict):
                raw = t["info"].get("lastPrice") or t["info"].get("markPrice")
            if raw is not None:
                pf = float(raw)
                if pf > 0:
                    ts = t.get("timestamp")
                    age_sec = max(0.0, (now_ms - float(ts)) / 1000.0) if ts else 0.0
                    candidates.append((pf, age_sec))
        except Exception:
            pass

    try:
        ohlcv = ex.fetch_ohlcv(symbol, "1m", limit=3)
        if ohlcv:
            row = ohlcv[-1]
            cl = float(row[4])
            ts_ms = float(row[0])
            if cl > 0:
                age_sec = max(0.0, (now_ms - ts_ms) / 1000.0)
                candidates.append((cl, age_sec))
    except Exception:
        pass

    return candidates


def fetch_fresh_last_price(
    symbol: str,
    *,
    max_stale_sec: float,
    prefer_ticker: bool,
) -> tuple[float | None, bool]:
    """
    Для отображения/трейлинга: цена с минимальным возрастом среди источников.
    Для срабатывания TP/SL в движке используйте min/max по fetch_mark_price_candidates.
    """
    candidates = fetch_mark_price_candidates(symbol, prefer_ticker=prefer_ticker)
    if not candidates:
        return None, True

    price, age_sec = min(candidates, key=lambda x: x[1])
    stale = age_sec > float(max_stale_sec)
    return price, stale


def get_usdt_linear_perpetual_symbols(*, refresh_sec: int = 3600, max_symbols: int = 0) -> list[str]:
    """
    Все активные linear USDT perpetual (swap) на Bybit, формат ccxt SYMBOL/USDT:USDT.
    Кэшируется на refresh_sec секунд. При большом числе пар увеличьте SCAN_INTERVAL_SEC и
    CCXT rateLimit, иначе возможны 10006/блокировки.
    """
    global _USDT_LINEAR_CACHE, _USDT_LINEAR_CACHE_TS
    now = time.time()
    if _USDT_LINEAR_CACHE is not None and now - _USDT_LINEAR_CACHE_TS < refresh_sec:
        return list(_USDT_LINEAR_CACHE)

    ex = create_public_exchange()
    ex.load_markets()
    out: list[str] = []
    for sym, m in ex.markets.items():
        if not m.get("active", True):
            continue
        if m.get("type") != "swap":
            continue
        if not m.get("linear"):
            continue
        if m.get("quote") != "USDT":
            continue
        out.append(sym)
    out.sort()
    n = len(out)
    if max_symbols > 0 and n > max_symbols:
        out = out[:max_symbols]
        logger.warning(
            "scan_all: loaded %s USDT linear perpetuals, capped to SCAN_ALL_MAX_SYMBOLS=%s",
            n,
            max_symbols,
        )
    else:
        logger.info("scan_all: loaded %s USDT linear perpetual symbols", len(out))
    if len(out) > 80:
        logger.warning(
            "scan_all: %s символов на цикл — сильная нагрузка на REST; увеличьте SCAN_INTERVAL_SEC "
            "и при необходимости задайте SCAN_ALL_MAX_SYMBOLS (или отключите scan_all).",
            len(out),
        )

    _USDT_LINEAR_CACHE = out
    _USDT_LINEAR_CACHE_TS = now
    return list(out)


def clear_usdt_linear_symbol_cache() -> None:
    global _USDT_LINEAR_CACHE, _USDT_LINEAR_CACHE_TS
    _USDT_LINEAR_CACHE = None
    _USDT_LINEAR_CACHE_TS = 0.0
