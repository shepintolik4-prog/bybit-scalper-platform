"""
Trend following: сильный ADX + направление +DI / −DI и согласованность с кратким импульсом.
Удержание и выход — общий трейлинг бота (risk.py / bot_engine).
"""
from __future__ import annotations

import pandas as pd

from app.config import get_settings
from app.ml.regime import MarketRegime, RegimeSnapshot
from app.strategies.types import StrategySignal


def signal_trend_following(
    df: pd.DataFrame,
    feats: dict[str, float],
    snap: RegimeSnapshot,
    reg: MarketRegime,
) -> StrategySignal | None:
    s = get_settings()
    adx_min = float(s.regime_adx_trend) * 0.88
    if snap.adx < adx_min:
        return None
    if reg not in (MarketRegime.TREND_UP, MarketRegime.TREND_DOWN):
        return None

    ret5 = float(feats.get("ret5", 0))
    macd_h = float(feats.get("macd_hist", 0))
    ts = float(snap.trend_strength)

    if reg == MarketRegime.TREND_UP:
        if snap.plus_di <= snap.minus_di * 1.02:
            return None
        if ret5 <= 0 and macd_h <= 0:
            return None
        edge = max(0.055, min(0.22, 0.09 + abs(ts) * 0.12))
        conf = max(0.52, min(0.78, 0.55 + min(snap.adx / 80.0, 0.22)))
        return StrategySignal(
            side="buy",
            edge=edge,
            confidence=conf,
            strategy_id="trend_following",
            skip_macd=True,
            tp_scale=1.15,
            details={
                "adx": round(snap.adx, 4),
                "plus_di": round(snap.plus_di, 4),
                "minus_di": round(snap.minus_di, 4),
                "trend_strength": round(ts, 4),
                "ret5": round(ret5, 6),
            },
        )

    if reg == MarketRegime.TREND_DOWN:
        if snap.minus_di <= snap.plus_di * 1.02:
            return None
        if ret5 >= 0 and macd_h >= 0:
            return None
        edge = -max(0.055, min(0.22, 0.09 + abs(ts) * 0.12))
        conf = max(0.52, min(0.78, 0.55 + min(snap.adx / 80.0, 0.22)))
        return StrategySignal(
            side="sell",
            edge=edge,
            confidence=conf,
            strategy_id="trend_following",
            skip_macd=True,
            tp_scale=1.15,
            details={
                "adx": round(snap.adx, 4),
                "plus_di": round(snap.plus_di, 4),
                "minus_di": round(snap.minus_di, 4),
                "trend_strength": round(ts, 4),
                "ret5": round(ret5, 6),
            },
        )
    return None
