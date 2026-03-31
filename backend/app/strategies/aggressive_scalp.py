"""
Высокочастотный скальпинг: RSI(k), отклонение от EMA, микротренд.
Включается только при AGGRESSIVE_SCALPING_MODE в Settings.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.config import get_settings
from app.ml.features import rsi as rsi_series
from app.ml.regime import MarketRegime, RegimeSnapshot
from app.strategies.types import StrategySignal


def signal_aggressive_scalp(
    df: pd.DataFrame,
    feats: dict[str, float],
    snap: RegimeSnapshot,
    reg: MarketRegime,
) -> StrategySignal | None:
    del feats, snap, reg
    s = get_settings()
    if not s.aggressive_scalping_mode:
        return None
    need = max(int(s.scalping_ema_period) + 5, int(s.scalping_rsi_period) + 5, 25)
    if df is None or len(df) < need:
        return None
    close = pd.to_numeric(df["close"], errors="coerce")
    if close.isna().all():
        return None
    rsi_p = int(s.scalping_rsi_period)
    ema_n = int(s.scalping_ema_period)
    rsi_s = rsi_series(close, rsi_p)
    ema = close.ewm(span=ema_n, adjust=False).mean()
    last = float(close.iloc[-1])
    ema_last = float(ema.iloc[-1])
    if ema_last == 0 or not (last == last):
        return None
    rsi_v = float(rsi_s.iloc[-1])
    if not (rsi_v == rsi_v):
        rsi_v = 50.0
    dev_pct = (last - ema_last) / abs(ema_last)
    thr = float(s.scalping_ema_deviation_pct)

    reasons: list[str] = []
    side: str | None = None
    edge_mag = float(s.scalping_micro_edge)
    conf = float(s.scalping_micro_confidence)

    if rsi_v <= float(s.scalping_rsi_oversold):
        side = "buy"
        edge_mag = float(s.scalping_rsi_edge)
        conf = float(s.scalping_rsi_confidence)
        reasons.append(f"rsi{rsi_p}_oversold")
    elif rsi_v >= float(s.scalping_rsi_overbought):
        side = "sell"
        edge_mag = float(s.scalping_rsi_edge)
        conf = float(s.scalping_rsi_confidence)
        reasons.append(f"rsi{rsi_p}_overbought")
    elif abs(dev_pct) >= thr:
        # ниже EMA на X% → покупка (откат / возврат к среднему)
        if dev_pct <= -thr:
            side = "buy"
            edge_mag = float(s.scalping_ema_edge)
            conf = float(s.scalping_ema_confidence)
            reasons.append("ema_deviation_long")
        else:
            side = "sell"
            edge_mag = float(s.scalping_ema_edge)
            conf = float(s.scalping_ema_confidence)
            reasons.append("ema_deviation_short")
    else:
        # микротренд — слабый, но частый вход
        if last >= ema_last:
            side = "buy"
            reasons.append("micro_trend_above_ema")
        else:
            side = "sell"
            reasons.append("micro_trend_below_ema")

    assert side is not None
    edge = edge_mag if side == "buy" else -edge_mag
    details: dict[str, Any] = {
        "entry_reasons": reasons,
        "rsi": round(rsi_v, 4),
        "ema": round(ema_last, 8),
        "close": round(last, 8),
        "dev_pct": round(dev_pct, 6),
    }
    return StrategySignal(
        side=side,  # type: ignore[arg-type]
        edge=edge,
        confidence=min(0.92, max(0.28, conf)),
        strategy_id="aggressive_scalp",
        skip_macd=True,
        tp_scale=float(s.scalping_tp_scale),
        details=details,
    )
