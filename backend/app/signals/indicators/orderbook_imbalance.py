from __future__ import annotations

from typing import Any


def orderbook_imbalance(
    orderbook: dict[str, Any],
    *,
    depth_levels: int = 10,
) -> float | None:
    """
    Returns imbalance in [-1, +1]:
    +1 = bids dominate, -1 = asks dominate.
    """
    try:
        bids = orderbook.get("bids") or []
        asks = orderbook.get("asks") or []
        n = max(1, int(depth_levels))
        bid_qty = sum(float(q) for _p, q in bids[:n] if q is not None)
        ask_qty = sum(float(q) for _p, q in asks[:n] if q is not None)
        denom = bid_qty + ask_qty
        if denom <= 0:
            return 0.0
        return float((bid_qty - ask_qty) / denom)
    except Exception:
        return None

