"""
Модель исполнения: spread, проскальзывание, задержка (в барах).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings


@dataclass(frozen=True)
class FillResult:
    fill_price: float
    spread_half: float
    slippage_bps_applied: float
    latency_bars: int


def apply_execution_price(
    mid_price: float,
    side: str,
    *,
    spread_bps: float | None = None,
    slippage_bps: float | None = None,
) -> FillResult:
    s = get_settings()
    sp = float(spread_bps if spread_bps is not None else s.exec_spread_bps)
    sl = float(slippage_bps if slippage_bps is not None else s.exec_slippage_bps)
    half_spread = mid_price * (sp / 10000.0) / 2.0
    slip = mid_price * (sl / 10000.0)
    if side == "buy":
        fill = mid_price + half_spread + slip
    else:
        fill = mid_price - half_spread - slip
    return FillResult(
        fill_price=float(fill),
        spread_half=float(half_spread),
        slippage_bps_applied=sl,
        latency_bars=int(s.exec_latency_bars),
    )


def latency_shift_index(base_idx: int, n_bars: int, max_len: int) -> int:
    return min(base_idx + max(0, n_bars), max_len - 1)
