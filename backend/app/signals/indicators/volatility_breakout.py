from __future__ import annotations

from collections.abc import Sequence


def true_range(high: float, low: float, prev_close: float) -> float:
    return float(max(high - low, abs(high - prev_close), abs(low - prev_close)))


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> float | None:
    n = int(period)
    if n <= 1 or len(closes) < (n + 1) or len(highs) < len(closes) or len(lows) < len(closes):
        return None
    trs: list[float] = []
    for i in range(1, len(closes)):
        trs.append(true_range(float(highs[i]), float(lows[i]), float(closes[i - 1])))
    if len(trs) < n:
        return None
    a = sum(trs[:n]) / n
    for v in trs[n:]:
        a = (a * (n - 1) + float(v)) / n
    return float(a)


def breakout_score(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float] | None = None,
    *,
    lookback: int = 20,
    atr_period: int = 14,
    atr_mult: float = 1.2,
) -> float | None:
    """
    0..1 score. Detects range expansion + close outside recent range by ATR multiple.
    """
    lb = int(lookback)
    if lb < 5 or len(closes) < (lb + 1):
        return None
    a = atr(highs, lows, closes, period=atr_period)
    if a is None or a <= 0:
        return None
    hi = max(float(x) for x in highs[-lb - 1 : -1])
    lo = min(float(x) for x in lows[-lb - 1 : -1])
    c = float(closes[-1])
    up = (c - hi) / a
    dn = (lo - c) / a
    x = max(0.0, up, dn)
    # Map: x >= atr_mult -> strong
    if x <= 0:
        return 0.0
    if x >= float(atr_mult) * 2.0:
        return 1.0
    return float(min(1.0, x / (float(atr_mult) * 2.0)))

