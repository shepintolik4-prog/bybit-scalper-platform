"""
Mean reversion (флэт): отклонение цены от SMA в z-score; быстрый тейк (tp_scale < 1).
"""
from __future__ import annotations

import pandas as pd

from app.config import get_settings
from app.ml.regime import MarketRegime, RegimeSnapshot
from app.strategies.types import StrategySignal


def signal_mean_reversion(
    df: pd.DataFrame,
    feats: dict[str, float],
    snap: RegimeSnapshot,
    reg: MarketRegime,
) -> StrategySignal | None:
    s = get_settings()
    if reg != MarketRegime.FLAT:
        return None
    close = df["close"].astype(float)
    if len(close) < 30:
        return None
    sma = close.rolling(20).mean().iloc[-1]
    std = close.rolling(20).std().iloc[-1]
    if std is None or float(std) <= 1e-12:
        return None
    z = (float(close.iloc[-1]) - float(sma)) / float(std)
    z_th = float(s.mean_reversion_z_threshold)
    if abs(z) < z_th:
        return None

    rsi = float(feats.get("rsi14", 50))
    edge_mag = max(0.05, min(0.14, 0.06 + (abs(z) - z_th) * 0.04))
    if z > 0:
        side = "sell"
        edge = -edge_mag
        conf = max(0.52, min(0.72, 0.54 + min((z - z_th) * 0.05, 0.16)))
        if rsi < 58:
            conf *= 0.92
    else:
        side = "buy"
        edge = edge_mag
        conf = max(0.52, min(0.72, 0.54 + min((-z - z_th) * 0.05, 0.16)))
        if rsi > 42:
            conf *= 0.92

    return StrategySignal(
        side=side,
        edge=edge,
        confidence=conf,
        strategy_id="mean_reversion",
        skip_macd=True,
        tp_scale=float(s.mean_reversion_tp_scale),
        details={
            "z_score": round(z, 4),
            "sma20": round(float(sma), 8),
            "rsi14": round(rsi, 4),
        },
    )
