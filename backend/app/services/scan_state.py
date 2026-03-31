"""
Потокобезопасный снимок последнего цикла сканирования для дашборда /metrics.
"""
from __future__ import annotations

import copy
import threading
import time
from typing import Any

_lock = threading.Lock()
_state: dict[str, Any] = {
    "updated_at": None,
    "tick_epoch": None,
    "scanned_symbols": [],
    "scanned_count": 0,
    "top_signals": [],
    "selected_symbol": None,
    "selected_composite": None,
    "rejects": [],
    "strategy_panel": {},
}


def append_reject(symbol: str, reason: str, **extra: Any) -> None:
    with _lock:
        rej = {"symbol": symbol, "reason": reason, "ts": time.time(), **extra}
        rj = _state["rejects"]
        if not isinstance(rj, list):
            rj = []
        rj.append(rej)
        _state["rejects"] = rj[-200:]


def set_snapshot(
    *,
    scanned_symbols: list[str],
    top_signals: list[dict[str, Any]],
    selected_symbol: str | None,
    selected_composite: float | None,
    strategy_panel: dict[str, Any] | None = None,
) -> None:
    with _lock:
        _state["updated_at"] = time.time()
        _state["tick_epoch"] = time.time()
        _state["scanned_symbols"] = list(scanned_symbols)
        _state["scanned_count"] = len(scanned_symbols)
        _state["top_signals"] = list(top_signals)
        _state["selected_symbol"] = selected_symbol
        _state["selected_composite"] = selected_composite
        if strategy_panel is not None:
            _state["strategy_panel"] = dict(strategy_panel)


def get_snapshot() -> dict[str, Any]:
    with _lock:
        return copy.deepcopy(_state)


def clear_rejects() -> None:
    with _lock:
        _state["rejects"] = []
