from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.signals.indicators.ema import ema
from app.signals.indicators.orderbook_imbalance import orderbook_imbalance
from app.signals.indicators.rsi import rsi
from app.signals.indicators.volatility_breakout import breakout_score
from app.signals.indicators.volume_spike import volume_spike_score


@dataclass(frozen=True)
class SignalWeights:
    w_orderbook: float
    w_volume: float
    w_breakout: float
    w_funding: float
    w_rsi: float


class SignalEngine:
    """
    Weighted signal combination. Output in [-1, +1] with debug components.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._s = s
        self._w = SignalWeights(
            w_orderbook=float(getattr(s, "signal_w_orderbook", 0.28)),
            w_volume=float(getattr(s, "signal_w_volume", 0.18)),
            w_breakout=float(getattr(s, "signal_w_breakout", 0.24)),
            w_funding=float(getattr(s, "signal_w_funding", 0.10)),
            w_rsi=float(getattr(s, "signal_w_rsi", 0.20)),
        )
        self._ema200_period = int(getattr(s, "trend_ema_period", 200))
        self._ob_depth_levels = int(getattr(s, "orderbook_depth_levels", 10))

    def evaluate(
        self,
        *,
        symbol: str,
        ohlcv: list[list[Any]],
        orderbook: dict[str, Any] | None = None,
        funding_rate: float | None = None,
    ) -> dict[str, Any]:
        # OHLCV shape: [ts, open, high, low, close, volume]
        closes = [float(r[4]) for r in ohlcv if len(r) >= 5]
        highs = [float(r[2]) for r in ohlcv if len(r) >= 3]
        lows = [float(r[3]) for r in ohlcv if len(r) >= 4]
        vols = [float(r[5]) for r in ohlcv if len(r) >= 6]

        ema200 = ema(closes[-(self._ema200_period * 2) :], self._ema200_period)
        trend_ok = None
        if ema200 is not None and closes:
            trend_ok = float(closes[-1]) >= float(ema200)

        r = rsi(closes[-300:], period=14)
        rsi_score = 0.0
        if r is not None:
            # Prefer mean-reversion bias: oversold => +, overbought => -
            if r <= 30:
                rsi_score = 1.0
            elif r >= 70:
                rsi_score = -1.0
            else:
                # map 30..70 to +0.2..-0.2
                rsi_score = float((50.0 - r) / 100.0)

        vb = volume_spike_score(vols, window=20) or 0.0
        # volume spike is directional only when paired with breakout; keep 0..1 here
        vol_score = float(vb)

        bo = breakout_score(highs, lows, closes, vols, lookback=20, atr_period=14, atr_mult=1.2)
        breakout = float(bo or 0.0)
        # Direction for breakout uses last close vs prior range
        breakout_dir = 0.0
        if len(closes) > 25:
            hi = max(highs[-21:-1])
            lo = min(lows[-21:-1])
            c = closes[-1]
            if c > hi:
                breakout_dir = 1.0
            elif c < lo:
                breakout_dir = -1.0

        ob = orderbook_imbalance(orderbook or {}, depth_levels=self._ob_depth_levels)
        ob_score = float(ob or 0.0)  # already [-1..+1]

        fund_score = 0.0
        if funding_rate is not None:
            fr = float(funding_rate)
            # Negative funding biases longs (+), positive funding biases shorts (-)
            # Clamp
            fund_score = max(-1.0, min(1.0, -fr / float(getattr(self._s, "funding_rate_scale", 0.001))))

        # Combine with weights
        raw = (
            self._w.w_orderbook * ob_score
            + self._w.w_volume * (vol_score * breakout_dir)
            + self._w.w_breakout * (breakout * breakout_dir)
            + self._w.w_funding * fund_score
            + self._w.w_rsi * rsi_score
        )

        # Apply trend filter: if below EMA200, suppress longs; if above, suppress shorts
        score = float(max(-1.0, min(1.0, raw)))
        if trend_ok is not None and closes and ema200 is not None:
            last = float(closes[-1])
            if last < float(ema200) and score > 0:
                score *= 0.35
            if last > float(ema200) and score < 0:
                score *= 0.35

        return {
            "symbol": symbol,
            "score": round(score, 6),
            "components": {
                "orderbook_imbalance": round(ob_score, 6),
                "volume_spike": round(vol_score, 6),
                "breakout": round(breakout, 6),
                "breakout_dir": round(breakout_dir, 6),
                "funding_bias": round(fund_score, 6),
                "rsi_bias": round(rsi_score, 6),
                "ema200": None if ema200 is None else round(float(ema200), 8),
                "trend_ok": trend_ok,
            },
            "weights": {
                "w_orderbook": self._w.w_orderbook,
                "w_volume": self._w.w_volume,
                "w_breakout": self._w.w_breakout,
                "w_funding": self._w.w_funding,
                "w_rsi": self._w.w_rsi,
            },
        }

