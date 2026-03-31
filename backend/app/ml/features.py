"""
Фичи для ML и режима. Важно: полный dropna() по всем колонкам давал пустой DataFrame
на «плоких» альткоинах (vol_cluster NaN, нулевой ATR в ADX).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.ml.regime import add_regime_columns

# После индикаторов нужно ≥ строк для устойчивой последней строки (macd slow 26 + запас)
MIN_FEATURE_ROWS_STRICT = 35
MIN_FEATURE_ROWS_RECOVERED = 22

_FEATURE_COLS = [
    "rsi14",
    "macd",
    "macd_hist",
    "atr14",
    "atr_pct",
    "ret5",
    "volatility20",
    "vol_ratio",
    "adx14",
    "vol_cluster_ratio",
]


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    line = ema_fast - ema_slow
    sig = ema(line, signal)
    hist = line - sig
    return line, sig, hist


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _compute_indicator_block(df: pd.DataFrame) -> pd.DataFrame:
    """Добавляет индикаторы + режим; без финального dropna."""
    out = df.copy()
    for c in ["open", "high", "low", "close", "volume"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out["rsi14"] = rsi(out["close"], 14)
    macd_line, macd_sig, macd_hist = macd(out["close"])
    out["macd"] = macd_line
    out["macd_signal"] = macd_sig
    out["macd_hist"] = macd_hist
    out["atr14"] = atr(out["high"], out["low"], out["close"], 14)
    close_pos = out["close"].astype(float).abs().clip(lower=1e-12)
    out["atr_pct"] = out["atr14"].astype(float) / close_pos
    out["ret1"] = out["close"].pct_change()
    out["ret5"] = out["close"].pct_change(5)
    out["volatility20"] = out["ret1"].rolling(20, min_periods=5).std()
    out["vol_ma20"] = out["volume"].rolling(20, min_periods=5).mean()
    out["vol_ratio"] = out["volume"] / out["vol_ma20"].replace(0, np.nan)
    out = add_regime_columns(out)
    return out


def _sanitize_indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.replace([np.inf, -np.inf], np.nan)
    out["vol_cluster_ratio"] = pd.to_numeric(out["vol_cluster_ratio"], errors="coerce").fillna(1.0).clip(0.1, 10.0)
    out["vol_ratio"] = pd.to_numeric(out["vol_ratio"], errors="coerce").fillna(1.0).clip(0.01, 50.0)
    out["atr14"] = pd.to_numeric(out["atr14"], errors="coerce").replace([np.inf, -np.inf], np.nan).clip(lower=0.0)
    out["atr_pct"] = pd.to_numeric(out["atr_pct"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    # Верхняя граница против делок с багами шкалы; без искусственного пола 1e-6 (он давал ложные atr_regime)
    out["atr_pct"] = out["atr_pct"].clip(lower=1e-12, upper=2.0)
    return out


def build_feature_frame(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """
    ohlcv columns: ts, open, high, low, close, volume
    Удаляем только строки с пропусками в колонках, нужных ML (не весь df целиком).
    """
    df = _compute_indicator_block(ohlcv)
    df = _sanitize_indicator_frame(df)
    return df.dropna(subset=_FEATURE_COLS)


def build_feature_frame_recovered(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """
    Мягкое восстановление: короткий ffill/bfill по индикаторам (без lookahead в прод —
    только хвост, уже закрытые свечи).
    """
    df = _compute_indicator_block(ohlcv)
    df = _sanitize_indicator_frame(df)
    ind_cols = [c for c in df.columns if c not in ("ts", "open", "high", "low", "close", "volume")]
    df[ind_cols] = df[ind_cols].ffill(limit=8).bfill(limit=8)
    df["vol_cluster_ratio"] = df["vol_cluster_ratio"].fillna(1.0).clip(0.1, 10.0)
    df["vol_ratio"] = df["vol_ratio"].fillna(1.0)
    df["rsi14"] = df["rsi14"].fillna(50.0)
    df["macd_hist"] = df["macd_hist"].fillna(0.0)
    df["macd"] = df["macd"].fillna(0.0)
    last_close = df["close"].ffill().bfill()
    df["atr14"] = df["atr14"].fillna(last_close * 0.01)
    lc = last_close.astype(float).abs().clip(lower=1e-12)
    df["atr_pct"] = df["atr_pct"].fillna((df["atr14"].astype(float) / lc).clip(1e-12, 2.0))
    df["adx14"] = df["adx14"].fillna(18.0)
    df["volatility20"] = df["volatility20"].fillna(0.01)
    df["ret5"] = df["ret5"].fillna(0.0)
    return df.dropna(subset=["close", "high", "low"])


def feature_frame_fallback_minimum(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """
    Последняя попытка: хвост OHLCV → recovered; если пусто — одна синтетическая строка
    (для predictor/meta при сработавшем rule-fallback).
    """
    if ohlcv is None or len(ohlcv) == 0:
        return pd.DataFrame()
    sub = ohlcv.tail(min(len(ohlcv), 120)).copy()
    rec = build_feature_frame_recovered(sub)
    if len(rec) >= 1:
        return rec
    c = float(pd.to_numeric(ohlcv["close"], errors="coerce").iloc[-1] or 1.0)
    h = float(pd.to_numeric(ohlcv["high"], errors="coerce").fillna(c).iloc[-1])
    lo = float(pd.to_numeric(ohlcv["low"], errors="coerce").fillna(c).iloc[-1])
    v = float(pd.to_numeric(ohlcv["volume"], errors="coerce").fillna(0.0).iloc[-1])
    ts = ohlcv["ts"].iloc[-1] if "ts" in ohlcv.columns else 0
    atr = max(abs(c) * 0.008, 1e-8)
    one = pd.DataFrame(
        [
            {
                "ts": ts,
                "open": c,
                "high": h,
                "low": lo,
                "close": c,
                "volume": max(v, 1e-6),
                "rsi14": 50.0,
                "macd": 0.0,
                "macd_signal": 0.0,
                "macd_hist": 0.0,
                "atr14": atr,
                "atr_pct": min(0.025, atr / max(abs(c), 1e-12)),
                "ret1": 0.0,
                "ret5": 0.0,
                "volatility20": 0.012,
                "vol_ma20": max(v, 1.0),
                "vol_ratio": 1.0,
            }
        ]
    )
    return add_regime_columns(one)


def diagnose_feature_frame_failure(ohlcv: pd.DataFrame) -> dict[str, Any]:
    """Диагностика для signal_reject empty_features (всё JSON-serializable)."""
    raw_len = len(ohlcv)
    cols = list(ohlcv.columns) if hasattr(ohlcv, "columns") else []
    out: dict[str, Any] = {
        "ohlcv_len": raw_len,
        "columns": cols,
        "stage": "unknown",
        "nan_pct_close": None,
        "feat_rows_strict": None,
        "feat_rows_recovered": None,
    }
    if raw_len == 0:
        out["stage"] = "ohlcv_zero"
        return out
    try:
        df = ohlcv.copy()
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "close" in df.columns:
            out["nan_pct_close"] = round(float(df["close"].isna().mean()), 6)
        strict = build_feature_frame(df)
        out["feat_rows_strict"] = len(strict)
        rec = build_feature_frame_recovered(df)
        out["feat_rows_recovered"] = len(rec)
        if len(strict) == 0 and len(rec) == 0:
            out["stage"] = "both_strict_and_recovered_empty"
        elif len(strict) == 0:
            out["stage"] = "strict_empty_recovered_ok"
        else:
            out["stage"] = "strict_nonempty"
    except Exception as e:
        out["stage"] = "exception"
        out["error"] = str(e)[:240]
    return out


def feature_vector_last(df: pd.DataFrame) -> dict[str, float]:
    row = df.iloc[-1]
    return {
        "rsi14": float(row["rsi14"]),
        "macd": float(row["macd"]),
        "macd_hist": float(row["macd_hist"]),
        "atr14": float(row["atr14"]),
        "atr_pct": float(row["atr_pct"]),
        "ret5": float(row["ret5"]),
        "volatility20": float(row["volatility20"]),
        "vol_ratio": float(row["vol_ratio"]),
        "adx14": float(row["adx14"]),
        "vol_cluster_ratio": float(row["vol_cluster_ratio"]),
    }
