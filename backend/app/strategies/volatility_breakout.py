"""
Volatility breakout: высокая волатильность / кластер + пробой локального high/low с объёмом.
"""
from __future__ import annotations

import pandas as pd

from app.config import get_settings
from app.ml.regime import MarketRegime, RegimeSnapshot
from app.strategies.types import StrategySignal


def signal_volatility_breakout(
    df: pd.DataFrame,
    feats: dict[str, float],
    snap: RegimeSnapshot,
    reg: MarketRegime,
) -> StrategySignal | None:
    s = get_settings()
    if reg != MarketRegime.HIGH_VOLATILITY:
        return None
    hi = df["high"].astype(float)
    lo = df["low"].astype(float)
    cl = df["close"].astype(float)
    vol = df["volume"].astype(float)
    if len(cl) < 25:
        return None

    hh = float(hi.iloc[-20:-1].max())
    ll = float(lo.iloc[-20:-1].min())
    last = float(cl.iloc[-1])
    v_last = float(vol.iloc[-1])
    v_med = float(vol.iloc[-20:-1].median()) or 1.0
    v_ratio = v_last / max(v_med, 1e-9)

    atr_pct = float(feats.get("atr_pct", 0.01))
    vcr = float(snap.vol_cluster_ratio)
    vcr_min = float(s.breakout_vol_cluster_min)
    vol_mult = float(s.breakout_volume_mult)

    if vcr < vcr_min and atr_pct < float(s.regime_atr_high_pct) * 0.92:
        return None
    if v_ratio < vol_mult:
        return None

    eps = max(1e-6, hh * 0.0004)
    if last > hh + eps:
        edge = max(0.06, min(0.2, 0.07 + atr_pct * 8))
        conf = max(0.54, min(0.8, 0.56 + min(v_ratio / 10, 0.2) + min(vcr / 25, 0.12)))
        return StrategySignal(
            side="buy",
            edge=edge,
            confidence=conf,
            strategy_id="volatility_breakout",
            skip_macd=True,
            tp_scale=float(s.breakout_tp_scale),
            details={
                "break_level": round(hh, 8),
                "vol_ratio": round(v_ratio, 4),
                "vol_cluster_ratio": round(vcr, 4),
                "atr_pct": round(atr_pct, 6),
            },
        )
    if last < ll - eps:
        edge = -max(0.06, min(0.2, 0.07 + atr_pct * 8))
        conf = max(0.54, min(0.8, 0.56 + min(v_ratio / 10, 0.2) + min(vcr / 25, 0.12)))
        return StrategySignal(
            side="sell",
            edge=edge,
            confidence=conf,
            strategy_id="volatility_breakout",
            skip_macd=True,
            tp_scale=float(s.breakout_tp_scale),
            details={
                "break_level": round(ll, 8),
                "vol_ratio": round(v_ratio, 4),
                "vol_cluster_ratio": round(vcr, 4),
                "atr_pct": round(atr_pct, 6),
            },
        )
    return None
