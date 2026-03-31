"""
Контроль корреляций между инструментами и суммарной экспозицией.
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd

from app.config import get_settings
from app.services import bybit_exchange

_CACHE: dict[str, Any] = {"ts": 0.0, "corr": None, "rets": None}


def fetch_returns_matrix(symbols: list[str], limit: int = 120) -> pd.DataFrame | None:
    cols: dict[str, pd.Series] = {}
    for sym in symbols:
        try:
            raw = bybit_exchange.fetch_ohlcv(sym, "5m", limit)
            if len(raw) < 30:
                continue
            df = pd.DataFrame(raw, columns=["ts", "o", "h", "l", "c", "v"])
            rets = df["c"].astype(float).pct_change().iloc[1:]
            cols[sym] = rets.reset_index(drop=True)
        except Exception:
            continue
    if len(cols) < 2:
        return None
    # align lengths
    m = min(len(v) for v in cols.values())
    data = {k: v.iloc[-m:].values for k, v in cols.items()}
    return pd.DataFrame(data)


def get_correlation_matrix(symbols: list[str], ttl_sec: float = 45.0) -> pd.DataFrame | None:
    now = time.time()
    key = ",".join(sorted(symbols))
    if _CACHE["corr"] is not None and now - float(_CACHE["ts"]) < ttl_sec and _CACHE.get("key") == key:
        return _CACHE["corr"]
    df = fetch_returns_matrix(symbols)
    if df is None or df.shape[1] < 2:
        return None
    corr = df.corr()
    _CACHE["ts"] = now
    _CACHE["corr"] = corr
    _CACHE["key"] = key
    return corr


def max_corr_with_others(symbol: str, open_symbols: list[str], symbols_universe: list[str]) -> float:
    syms = list({symbol, *open_symbols, *symbols_universe})
    corr = get_correlation_matrix(syms)
    if corr is None or symbol not in corr.columns:
        return 0.0
    others = [s for s in open_symbols if s != symbol]
    if not others:
        return 0.0
    row = corr.loc[symbol, others]
    if not len(others):
        return 0.0
    vals = np.abs(pd.to_numeric(row, errors="coerce").values)
    if np.all(np.isnan(vals)):
        return 0.0
    return float(np.nanmax(vals))


def passes_correlation_gate(
    candidate: str,
    open_symbols: list[str],
    universe: list[str],
) -> tuple[bool, str]:
    s = get_settings()
    if not open_symbols:
        return True, "ok"
    m = max_corr_with_others(candidate, open_symbols, universe)
    if m >= s.corr_max_pair:
        return False, f"correlation_too_high max_abs={m:.3f}>={s.corr_max_pair}"
    return True, f"corr_ok max_abs={m:.3f}"


def total_exposure_ratio(positions: list[tuple[float, int]], equity: float) -> float:
    """positions: (margin_usdt, leverage). Суммарная номинальная / equity."""
    if equity <= 0:
        return 1.0
    notional = sum(float(m) * int(lev) for m, lev in positions)
    return notional / equity
