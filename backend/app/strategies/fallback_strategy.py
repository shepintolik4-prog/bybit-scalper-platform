"""
Резерв, если основной feature pipeline не дал строку для ML: EMA cross, Donchian breakout, volume spike.
Используется только при включённом FALLBACK_RULE_ENABLED и после исчерпания recovered-фрейма.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.ml.regime import MarketRegime, RegimeSnapshot
from app.strategies.types import StrategySignal

MIN_BARS = 55


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def signal_fallback_technical(
    df: pd.DataFrame,
    feats: dict[str, float],
    snap: RegimeSnapshot,
    reg: MarketRegime,
) -> StrategySignal | None:
    """
    Совместимо с RULE_STRATEGY_REGISTRY: feats/snap/reg могут быть заглушками.
    """
    del feats, snap, reg
    if df is None or len(df) < MIN_BARS:
        return None
    try:
        close = pd.to_numeric(df["close"], errors="coerce")
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        vol = pd.to_numeric(df["volume"], errors="coerce")
    except Exception:
        return None
    if close.isna().all():
        return None

    ema_f = _ema(close, 12)
    ema_s = _ema(close, 26)
    bull_cross = ema_f.iloc[-2] <= ema_s.iloc[-2] and ema_f.iloc[-1] > ema_s.iloc[-1]
    bear_cross = ema_f.iloc[-2] >= ema_s.iloc[-2] and ema_f.iloc[-1] < ema_s.iloc[-1]

    look = 20
    hh = float(high.iloc[-look:-1].max())
    ll = float(low.iloc[-look:-1].min())
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    breakout_up = last > hh and prev <= hh
    breakout_down = last < ll and prev >= ll

    vma = vol.rolling(20, min_periods=5).mean()
    v_last = float(vol.iloc[-1]) if not pd.isna(vol.iloc[-1]) else 0.0
    v_ma = float(vma.iloc[-1]) if not pd.isna(vma.iloc[-1]) else max(v_last, 1e-9)
    vol_spike = v_last > v_ma * 1.8

    score_buy = float(bull_cross) + float(breakout_up) + (0.5 if vol_spike and last > float(close.iloc[-3]) else 0.0)
    score_sell = float(bear_cross) + float(breakout_down) + (0.5 if vol_spike and last < float(close.iloc[-3]) else 0.0)

    details: dict[str, Any] = {
        "ema_cross_bull": bull_cross,
        "ema_cross_bear": bear_cross,
        "breakout_up": breakout_up,
        "breakout_down": breakout_down,
        "vol_spike": vol_spike,
        "score_buy": score_buy,
        "score_sell": score_sell,
    }

    if score_buy >= 1.0 and score_buy >= score_sell:
        conf = min(0.72, 0.45 + 0.12 * score_buy)
        return StrategySignal(
            side="buy",
            edge=0.09 + 0.02 * min(score_buy, 2.0),
            confidence=conf,
            strategy_id="fallback_technical",
            skip_macd=True,
            tp_scale=1.0,
            details=details,
        )
    if score_sell >= 1.0 and score_sell > score_buy:
        conf = min(0.72, 0.45 + 0.12 * score_sell)
        return StrategySignal(
            side="sell",
            edge=-(0.09 + 0.02 * min(score_sell, 2.0)),
            confidence=conf,
            strategy_id="fallback_technical",
            skip_macd=True,
            tp_scale=1.0,
            details=details,
        )
    return None


def signal_fallback_from_ohlcv_only(df: pd.DataFrame) -> StrategySignal | None:
    """Вызов без regime-контекста (для bot_engine до classify)."""
    dummy_snap = RegimeSnapshot(
        regime=MarketRegime.FLAT,
        adx=18.0,
        vol_cluster_ratio=1.0,
        plus_di=25.0,
        minus_di=25.0,
        trend_strength=0.0,
    )
    return signal_fallback_technical(df, {}, dummy_snap, MarketRegime.FLAT)
