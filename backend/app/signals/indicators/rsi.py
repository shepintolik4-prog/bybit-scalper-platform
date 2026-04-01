from __future__ import annotations

from collections.abc import Sequence


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    n = int(period)
    if n <= 1 or len(closes) < (n + 1):
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, n + 1):
        ch = float(closes[i]) - float(closes[i - 1])
        if ch >= 0:
            gains += ch
        else:
            losses += -ch
    avg_gain = gains / n
    avg_loss = losses / n
    for i in range(n + 1, len(closes)):
        ch = float(closes[i]) - float(closes[i - 1])
        g = ch if ch > 0 else 0.0
        l = -ch if ch < 0 else 0.0
        avg_gain = (avg_gain * (n - 1) + g) / n
        avg_loss = (avg_loss * (n - 1) + l) / n
    if avg_loss <= 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))

