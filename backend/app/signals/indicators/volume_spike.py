from __future__ import annotations

import math
from collections.abc import Sequence


def volume_spike_score(volumes: Sequence[float], *, window: int = 20) -> float | None:
    """
    Returns 0..1 score: 1.0 = сильный спайк.
    """
    w = int(window)
    if w <= 3 or len(volumes) < (w + 1):
        return None
    tail = [float(x) for x in volumes[-(w + 1) : -1]]
    cur = float(volumes[-1])
    if cur <= 0:
        return 0.0
    m = sorted(tail)[len(tail) // 2]  # median
    if m <= 0:
        return 0.0
    ratio = cur / m
    # Smooth map: ratio 1.0 -> 0, ratio 2.0 -> ~0.5, ratio 4.0 -> ~0.8
    x = max(0.0, ratio - 1.0)
    return float(1.0 - math.exp(-x / 1.25))

