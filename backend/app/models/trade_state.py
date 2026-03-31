"""
Жизненный цикл сделки/позиции (production state machine).
Единая терминология для TradeRecord.lifecycle_state (и опционально Position).
"""
from __future__ import annotations

from enum import Enum
from typing import FrozenSet


class PositionLifecycleState(str, Enum):
    NEW = "new"
    PENDING = "pending"
    PARTIAL = "partial"
    FILLED = "filled"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


# Допустимые переходы (строгий граф)
_VALID: dict[PositionLifecycleState, FrozenSet[PositionLifecycleState]] = {
    PositionLifecycleState.NEW: frozenset({PositionLifecycleState.PENDING}),
    PositionLifecycleState.PENDING: frozenset(
        {
            PositionLifecycleState.PARTIAL,
            PositionLifecycleState.FILLED,
            PositionLifecycleState.FAILED,
        }
    ),
    PositionLifecycleState.PARTIAL: frozenset(
        {PositionLifecycleState.FILLED, PositionLifecycleState.FAILED}
    ),
    PositionLifecycleState.FILLED: frozenset(
        {
            PositionLifecycleState.CLOSING,
            PositionLifecycleState.CLOSED,
            PositionLifecycleState.FAILED,
        }
    ),
    PositionLifecycleState.CLOSING: frozenset(
        {PositionLifecycleState.CLOSED, PositionLifecycleState.FAILED}
    ),
    PositionLifecycleState.CLOSED: frozenset(),
    PositionLifecycleState.FAILED: frozenset(),
}


class InvalidLifecycleTransition(ValueError):
    """Запрещённый переход по state machine."""


def parse_lifecycle(raw: str | None) -> PositionLifecycleState:
    if not raw:
        return PositionLifecycleState.NEW
    try:
        return PositionLifecycleState(raw.lower().strip())
    except ValueError:
        return PositionLifecycleState.NEW


def can_transition(
    current: PositionLifecycleState,
    target: PositionLifecycleState,
    *,
    force: bool = False,
) -> bool:
    if force:
        return True
    if current == target:
        return True
    allowed = _VALID.get(current)
    if allowed is None:
        return False
    return target in allowed


def require_transition(
    current: PositionLifecycleState,
    target: PositionLifecycleState,
    *,
    force: bool = False,
) -> None:
    if not can_transition(current, target, force=force):
        raise InvalidLifecycleTransition(f"{current.value} -> {target.value} not allowed")
