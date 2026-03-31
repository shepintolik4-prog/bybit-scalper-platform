"""
Централизованное управление lifecycle_state (state machine) + блокировки по (symbol, mode).
Единственная рекомендуемая точка смены состояния для TradeRecord / Position.
Поток: синхронный (threading.RLock). asyncio.Lock не используется — воркер бота не async.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Generator

import sentry_sdk
from sqlalchemy.orm import Session

from app.models.orm import Position, TradeRecord
from app.models.trade_state import (
    InvalidLifecycleTransition,
    PositionLifecycleState,
    parse_lifecycle,
    require_transition,
)
from app.monitoring.prometheus_metrics import invalid_state_transition_total, race_condition_detected_total

logger = logging.getLogger("scalper.state_manager")

_lock_registry_lock = threading.Lock()
_symbol_locks: dict[str, threading.RLock] = {}


def _lock_key(symbol: str, mode: str) -> str:
    return f"{mode.strip().lower()}:{symbol.strip()}"


def _get_symbol_lock(key: str) -> threading.RLock:
    with _lock_registry_lock:
        if key not in _symbol_locks:
            _symbol_locks[key] = threading.RLock()
        return _symbol_locks[key]


@contextmanager
def symbol_operation_lock(symbol: str, mode: str) -> Generator[None, None, None]:
    """Одна активная операция на символ+режим (вход/выход/синх)."""
    lk = _get_symbol_lock(_lock_key(symbol, mode))
    acquired = lk.acquire(timeout=30.0)
    if not acquired:
        try:
            race_condition_detected_total.labels(kind="lock_timeout").inc()
        except Exception:
            pass
        logger.error("state_manager lock timeout %s %s", symbol, mode)
        raise TimeoutError(f"symbol lock timeout {symbol} {mode}")
    try:
        yield
    finally:
        lk.release()


def current_trade_state(tr: TradeRecord) -> PositionLifecycleState:
    raw = getattr(tr, "lifecycle_state", None) or _infer_from_legacy_status(tr)
    return parse_lifecycle(raw)


def _infer_from_legacy_status(tr: TradeRecord) -> str:
    st = (tr.status or "").lower()
    if st == "pending":
        return PositionLifecycleState.PENDING.value
    if st == "open":
        os_ = (tr.order_status or "").lower()
        if os_ == "partially_filled":
            return PositionLifecycleState.PARTIAL.value
        return PositionLifecycleState.FILLED.value
    if st == "failed":
        return PositionLifecycleState.FAILED.value
    if st == "closed":
        return PositionLifecycleState.CLOSED.value
    return PositionLifecycleState.NEW.value


def transition_trade_record(
    tr: TradeRecord,
    target: PositionLifecycleState,
    *,
    force: bool = False,
    db: Session | None = None,
) -> bool:
    """
    Сменить lifecycle_state у TradeRecord с проверкой графа.
    Возвращает False при запрете (метрика + Sentry), True при успехе.
    """
    cur = current_trade_state(tr)
    try:
        require_transition(cur, target, force=force)
    except InvalidLifecycleTransition as e:
        try:
            invalid_state_transition_total.labels(from_state=cur.value, to_state=target.value).inc()
        except Exception:
            pass
        try:
            sentry_sdk.capture_message(f"lifecycle: {e}", level="warning")
        except Exception:
            pass
        logger.warning("invalid lifecycle transition trade_id=%s %s -> %s", tr.id, cur, target)
        return False
    tr.lifecycle_state = target.value
    if db is not None:
        db.flush()
    return True


def transition_position_lifecycle(pos: Position | None, target: PositionLifecycleState, db: Session | None = None) -> None:
    if pos is None:
        return
    if not hasattr(pos, "lifecycle_state"):
        return
    cur = parse_lifecycle(getattr(pos, "lifecycle_state", None) or PositionLifecycleState.FILLED.value)
    try:
        require_transition(cur, target, force=False)
    except InvalidLifecycleTransition:
        # позиция может пропускать PENDING — допускаем FILLED -> CLOSING напрямую
        require_transition(cur, target, force=True)
    pos.lifecycle_state = target.value
    if db is not None:
        db.flush()


def can_start_live_entry(db: Session, symbol: str, mode: str = "live") -> bool:
    """True, если по символу нет pending/open — защита от двойного входа."""
    row = (
        db.query(TradeRecord)
        .filter(
            TradeRecord.symbol == symbol,
            TradeRecord.mode == mode,
            TradeRecord.status.in_(("pending", "open")),
        )
        .first()
    )
    if row is None:
        return True
    try:
        race_condition_detected_total.labels(kind="double_open_prevented").inc()
    except Exception:
        pass
    return False


# Совместимость со старым именем
assert_no_active_live_intent = can_start_live_entry
