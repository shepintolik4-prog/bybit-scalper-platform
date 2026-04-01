from __future__ import annotations

from collections.abc import Sequence


def ema(values: Sequence[float], period: int) -> float | None:
    n = int(period)
    if n <= 1:
        return float(values[-1]) if values else None
    if len(values) < n:
        return None
    k = 2.0 / (n + 1.0)
    e = float(values[0])
    for v in values[1:]:
        e = float(v) * k + e * (1.0 - k)
    return float(e)

