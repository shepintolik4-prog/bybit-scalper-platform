"""
Детекция режима рынка: ADX, кластеризация волатильности (short/long vol), классификация.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from app.config import get_settings


class MarketRegime(str, Enum):
    HIGH_VOLATILITY = "high_volatility"
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    FLAT = "flat"


@dataclass(frozen=True)
class RegimeSnapshot:
    regime: MarketRegime
    adx: float
    vol_cluster_ratio: float
    plus_di: float
    minus_di: float
    trend_strength: float  # -1..1 эвристика


def _adx_di(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean().clip(lower=1e-12)
    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).clip(0, 100)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx, plus_di, minus_di


def add_regime_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Ожидает колонки open, high, low, close; добавляет adx14, plus_di, minus_di, vol_cluster_ratio."""
    out = df.copy()
    adx, pdi, mdi = _adx_di(out["high"], out["low"], out["close"], 14)
    out["adx14"] = adx
    out["plus_di14"] = pdi
    out["minus_di14"] = mdi
    ret = out["close"].pct_change()
    # Короткое / длинное окно: нижняя граница std, чтобы не заливать NaN при «плоских» 60 свечах (альткоины)
    v_s = ret.rolling(5, min_periods=2).std().fillna(0.0)
    v_l = ret.rolling(60, min_periods=10).std().clip(lower=1e-8)
    ratio = v_s / v_l
    ratio = ratio.replace([np.inf, -np.inf], np.nan).fillna(1.0)
    out["vol_cluster_ratio"] = ratio.clip(0.1, 10.0)
    return out


def classify_regime_row(row: pd.Series) -> RegimeSnapshot:
    s = get_settings()
    adx_v = float(row["adx14"])
    vcr = float(row["vol_cluster_ratio"])
    pdi = float(row.get("plus_di14", 50))
    mdi = float(row.get("minus_di14", 50))
    atr_pct = float(row.get("atr_pct", 0.01))
    macd_h = float(row.get("macd_hist", 0))
    ret5 = float(row.get("ret5", 0))

    trend_strength = np.tanh((pdi - mdi) / 50.0) * (adx_v / 50.0)

    if atr_pct >= s.regime_atr_high_pct or vcr >= s.regime_vol_cluster_high:
        regime = MarketRegime.HIGH_VOLATILITY
    elif adx_v >= s.regime_adx_trend and macd_h >= 0 and ret5 > 0:
        regime = MarketRegime.TREND_UP
    elif adx_v >= s.regime_adx_trend and macd_h <= 0 and ret5 < 0:
        regime = MarketRegime.TREND_DOWN
    elif adx_v >= s.regime_adx_trend:
        regime = MarketRegime.TREND_UP if pdi > mdi else MarketRegime.TREND_DOWN
    else:
        regime = MarketRegime.FLAT

    return RegimeSnapshot(
        regime=regime,
        adx=adx_v,
        vol_cluster_ratio=vcr,
        plus_di=pdi,
        minus_di=mdi,
        trend_strength=float(trend_strength),
    )


def regime_multipliers(reg: MarketRegime) -> dict[str, float]:
    """Множители: edge (порог сигнала), sl (ширина стопа), size (позиция), allow_scalp."""
    s = get_settings()
    if reg == MarketRegime.HIGH_VOLATILITY:
        return {"edge": s.regime_m_high_edge, "sl": s.regime_m_high_sl, "size": s.regime_m_high_size, "allow": 1.0}
    if reg == MarketRegime.TREND_UP or reg == MarketRegime.TREND_DOWN:
        return {"edge": s.regime_m_trend_edge, "sl": s.regime_m_trend_sl, "size": s.regime_m_trend_size, "allow": 1.0}
    return {"edge": s.regime_m_flat_edge, "sl": s.regime_m_flat_sl, "size": s.regime_m_flat_size, "allow": s.regime_flat_trade_allow}


def should_trade_regime(reg: MarketRegime) -> bool:
    s = get_settings()
    if reg == MarketRegime.FLAT:
        return float(s.regime_flat_trade_allow) >= 0.5
    return True
