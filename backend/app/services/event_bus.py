"""
Синхронная шина событий (in-process). Расширяемо под out-of-process позже.
События: signal_generated, order_created, order_filled, position_closed, ...
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger("scalper.event_bus")

_lock = threading.Lock()
_handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)

EventHandler = Callable[[dict[str, Any]], None]


def subscribe(event_type: str, handler: EventHandler) -> None:
    with _lock:
        _handlers[event_type].append(handler)


def unsubscribe(event_type: str, handler: EventHandler) -> None:
    with _lock:
        lst = _handlers.get(event_type)
        if not lst:
            return
        try:
            lst.remove(handler)
        except ValueError:
            pass


def emit(event_type: str, payload: dict[str, Any] | None = None) -> None:
    data = dict(payload or {})
    data.setdefault("event_type", event_type)
    handlers: list[EventHandler]
    with _lock:
        handlers = list(_handlers.get(event_type, []))
    for h in handlers:
        try:
            h(data)
        except Exception:
            logger.exception("event_bus handler failed event=%s", event_type)


# Константы имён
E_SIGNAL_GENERATED = "signal_generated"
E_ORDER_CREATED = "order_created"
E_ORDER_FILLED = "order_filled"
E_POSITION_CLOSED = "position_closed"
E_KILL_SWITCH = "kill_switch_triggered"
E_CONSISTENCY_ERROR = "consistency_error"
E_INVALID_TRANSITION = "invalid_state_transition"
