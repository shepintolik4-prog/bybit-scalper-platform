"""
Пакет стратегий для FULL_AGGRESSIVE_MAX_FLOW (максимум сделок, минимум фильтров).
A: RSI scalp · B: EMA deviation · C: momentum candles · D: micro breakout.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.config import get_settings
from app.ml.features import rsi as rsi_series
from app.strategies.types import StrategySignal


def _need_len(settings: Any) -> int:
    s = settings
    return max(
        int(s.full_aggressive_rsi_period) + 3,
        int(s.full_aggressive_momentum_bars) + 2,
        int(s.full_aggressive_breakout_lookback) + 3,
        25,
    )


def pick_max_flow_signal(df: pd.DataFrame, feats: dict[str, float]) -> StrategySignal | None:
    """
    Первая сработавшая стратегия (A→D). Каждая сделка получает strategy_id и entry_reason в details.
    """
    s = get_settings()
    if not s.full_aggressive_max_flow:
        return None
    need = _need_len(s)
    if df is None or len(df) < need:
        return None
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    if close.isna().all():
        return None

    last = float(close.iloc[-1])
    if not (last == last) or last <= 0:
        return None

    ema20_series = close.ewm(span=20, adjust=False).mean()
    ema20_last = float(ema20_series.iloc[-1]) if ema20_series.iloc[-1] == ema20_series.iloc[-1] else 0.0

    rsi_p = int(s.full_aggressive_rsi_period)
    rsi_s = rsi_series(close, rsi_p)
    rsi_v = float(rsi_s.iloc[-1])
    if not (rsi_v == rsi_v):
        rsi_v = 50.0

    # --- A: RSI scalp ---
    if rsi_v <= float(s.full_aggressive_rsi_oversold):
        edge = 0.12
        return StrategySignal(
            side="buy",
            edge=edge,
            confidence=0.55,
            strategy_id="maxflow_rsi_scalp",
            skip_macd=True,
            tp_scale=float(s.full_aggressive_tp_scale),
            details={
                "entry_reason": f"rsi{rsi_p}_oversold",
                "rsi": round(rsi_v, 4),
                "atr_pct": round(float(feats.get("atr_pct", 0)), 6),
                "ema20": round(ema20_last, 8),
            },
        )
    if rsi_v >= float(s.full_aggressive_rsi_overbought):
        return StrategySignal(
            side="sell",
            edge=-0.12,
            confidence=0.55,
            strategy_id="maxflow_rsi_scalp",
            skip_macd=True,
            tp_scale=float(s.full_aggressive_tp_scale),
            details={
                "entry_reason": f"rsi{rsi_p}_overbought",
                "rsi": round(rsi_v, 4),
                "atr_pct": round(float(feats.get("atr_pct", 0)), 6),
                "ema20": round(ema20_last, 8),
            },
        )

    ema_last = ema20_last
    if ema_last > 0:
        dev_pct = (last - ema_last) / abs(ema_last)
        thr = float(s.full_aggressive_ema_dev_pct)
        # --- B: EMA deviation (mean-reversion style) ---
        if dev_pct <= -thr:
            return StrategySignal(
                side="buy",
                edge=0.09,
                confidence=0.48,
                strategy_id="maxflow_ema_deviation",
                skip_macd=True,
                tp_scale=float(s.full_aggressive_tp_scale),
                details={
                    "entry_reason": "price_below_ema20_pct",
                    "dev_pct": round(dev_pct, 6),
                    "ema20": round(ema_last, 8),
                    "rsi": round(rsi_v, 4),
                    "atr_pct": round(float(feats.get("atr_pct", 0)), 6),
                },
            )
        if dev_pct >= thr:
            return StrategySignal(
                side="sell",
                edge=-0.09,
                confidence=0.48,
                strategy_id="maxflow_ema_deviation",
                skip_macd=True,
                tp_scale=float(s.full_aggressive_tp_scale),
                details={
                    "entry_reason": "price_above_ema20_pct",
                    "dev_pct": round(dev_pct, 6),
                    "ema20": round(ema_last, 8),
                    "rsi": round(rsi_v, 4),
                    "atr_pct": round(float(feats.get("atr_pct", 0)), 6),
                },
            )

    # --- C: Momentum (N candles same direction) ---
    n = max(2, int(s.full_aggressive_momentum_bars))
    if len(close) >= n + 1:
        seg = close.iloc[-n:]
        diffs = seg.diff().dropna()
        if len(diffs) == n - 1 and (diffs > 0).all():
            return StrategySignal(
                side="buy",
                edge=0.08,
                confidence=0.42,
                strategy_id="maxflow_momentum_run",
                skip_macd=True,
                tp_scale=float(s.full_aggressive_tp_scale),
                details={
                    "entry_reason": f"{n}_green_closes",
                    "rsi": round(rsi_v, 4),
                    "atr_pct": round(float(feats.get("atr_pct", 0)), 6),
                    "ema20": round(ema20_last, 8) if ema20_last > 0 else None,
                },
            )
        if len(diffs) == n - 1 and (diffs < 0).all():
            return StrategySignal(
                side="sell",
                edge=-0.08,
                confidence=0.42,
                strategy_id="maxflow_momentum_run",
                skip_macd=True,
                tp_scale=float(s.full_aggressive_tp_scale),
                details={
                    "entry_reason": f"{n}_red_closes",
                    "rsi": round(rsi_v, 4),
                    "atr_pct": round(float(feats.get("atr_pct", 0)), 6),
                    "ema20": round(ema20_last, 8) if ema20_last > 0 else None,
                },
            )

    # --- D: Micro breakout ---
    lb = max(3, int(s.full_aggressive_breakout_lookback))
    if len(high) >= lb + 1:
        prev_high = float(high.iloc[-(lb + 1) : -1].max())
        prev_low = float(low.iloc[-(lb + 1) : -1].min())
        lh = float(high.iloc[-1])
        ll = float(low.iloc[-1])
        if lh > prev_high and prev_high > 0:
            return StrategySignal(
                side="buy",
                edge=0.085,
                confidence=0.45,
                strategy_id="maxflow_micro_breakout",
                skip_macd=True,
                tp_scale=float(s.full_aggressive_tp_scale),
                details={
                    "entry_reason": "break_local_high",
                    "prev_high": round(prev_high, 8),
                    "rsi": round(rsi_v, 4),
                    "atr_pct": round(float(feats.get("atr_pct", 0)), 6),
                },
            )
        if ll < prev_low and prev_low > 0:
            return StrategySignal(
                side="sell",
                edge=-0.085,
                confidence=0.45,
                strategy_id="maxflow_micro_breakout",
                skip_macd=True,
                tp_scale=float(s.full_aggressive_tp_scale),
                details={
                    "entry_reason": "break_local_low",
                    "prev_low": round(prev_low, 8),
                    "rsi": round(rsi_v, 4),
                    "atr_pct": round(float(feats.get("atr_pct", 0)), 6),
                },
            )

    return None


def force_volatility_entry_signal(df: pd.DataFrame, feats: dict[str, float]) -> StrategySignal:
    """Принудительный вход по направлению последней свечи + высокий ATR% в приоритете."""
    s = get_settings()
    close = pd.to_numeric(df["close"], errors="coerce")
    op = pd.to_numeric(df["open"], errors="coerce")
    lc = float(close.iloc[-1])
    lo = float(op.iloc[-1])
    side: Any = "buy" if lc >= lo else "sell"
    edge = 0.06 if side == "buy" else -0.06
    return StrategySignal(
        side=side,
        edge=edge,
        confidence=float(s.full_aggressive_min_conf),
        strategy_id="maxflow_force_volatility",
        skip_macd=True,
        tp_scale=float(s.full_aggressive_tp_scale),
        details={
            "entry_reason": "force_trade_volatility_or_idle",
            "last_candle_bias": "bullish" if side == "buy" else "bearish",
            "atr_pct": round(float(feats.get("atr_pct", 0)), 6),
            "rsi": round(float(feats.get("rsi14", 50)), 4),
        },
    )
