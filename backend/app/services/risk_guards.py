"""
Ограничения частоты входов, экспозиции на символ, circuit breaker после серии ошибок.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from app.config import get_settings
from app.monitoring.prometheus_metrics import circuit_breaker_open_gauge

_lock = threading.Lock()
_open_timestamps: deque[float] = deque(maxlen=500)
_failure_timestamps: deque[float] = deque(maxlen=200)


class RiskGuards:
    def __init__(self) -> None:
        self._circuit_open_until: float = 0.0

    def record_open_attempt(self) -> None:
        with _lock:
            _open_timestamps.append(time.time())

    def allow_new_trade_this_minute(self) -> bool:
        s = get_settings()
        if s.full_aggressive_max_flow:
            m = int(s.full_aggressive_max_trades_per_minute)
        else:
            m = int(s.scalping_max_trades_per_minute) if s.aggressive_scalping_mode else int(s.max_trades_per_minute)
        if m <= 0:
            return True
        now = time.time()
        with _lock:
            while _open_timestamps and now - _open_timestamps[0] > 60.0:
                _open_timestamps.popleft()
            return len(_open_timestamps) < m

    def record_execution_failure(self) -> None:
        s = get_settings()
        now = time.time()
        with _lock:
            _failure_timestamps.append(now)
            win = float(s.circuit_breaker_window_sec)
            need = int(s.circuit_breaker_failure_threshold)
            if need <= 0 or win <= 0:
                return
            while _failure_timestamps and now - _failure_timestamps[0] > win:
                _failure_timestamps.popleft()
            if len(_failure_timestamps) >= need:
                cool = float(s.circuit_breaker_cooldown_sec)
                self._circuit_open_until = now + cool
                try:
                    circuit_breaker_open_gauge.set(1.0)
                except Exception:
                    pass

    def circuit_allows_new_trades(self) -> bool:
        if time.time() >= self._circuit_open_until:
            try:
                circuit_breaker_open_gauge.set(0.0)
            except Exception:
                pass
            return True
        return False

    def exposure_ok_for_symbol(
        self,
        symbol: str,
        new_margin_usdt: float,
        new_lev: int,
        open_triples: list[tuple[str, float, int]],
        equity: float,
    ) -> tuple[bool, str]:
        """Лимит гросс-экспозиции на один символ (USDT notional / equity)."""
        s = get_settings()
        if s.full_aggressive_max_flow and s.full_aggressive_skip_symbol_exposure_cap:
            return True, ""
        cap = float(s.max_exposure_per_symbol_ratio)
        if cap <= 0 or equity <= 0:
            return True, ""
        existing = sum(
            float(sz) * int(lv) for sym, sz, lv in open_triples if sym == symbol
        )
        add = float(new_margin_usdt) * int(new_lev)
        total_sym = (existing + add) / equity
        if total_sym > cap:
            return False, f"symbol_exposure {total_sym:.3f}>{cap:.3f}"
        return True, ""


_guards_singleton: RiskGuards | None = None


def global_guards() -> RiskGuards:
    global _guards_singleton
    if _guards_singleton is None:
        _guards_singleton = RiskGuards()
    return _guards_singleton
