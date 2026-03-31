"""Типы сигналов rule-based стратегий."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class StrategySignal:
    side: Literal["buy", "sell"]
    """Направление."""
    edge: float
    """Signed edge proxy для общего пайплайна (buy > 0 как у ML)."""
    confidence: float
    strategy_id: str
    skip_macd: bool = False
    tp_scale: float = 1.0
    details: dict[str, Any] = field(default_factory=dict)
