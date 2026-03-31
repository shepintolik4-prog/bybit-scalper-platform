"""
Глобальный kill switch: пауза новых входов при критических условиях.
Интеграция с trading_control_store (source=kill_switch).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import sentry_sdk
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.orm import BotSettings, EquityPoint
from app.monitoring.prometheus_metrics import kill_switch_triggered_total, system_health_gauge
from app.services.alerts import send_alert
from app.services.event_bus import E_KILL_SWITCH, emit
from app.services.trading_control_store import load_control, set_pause

logger = logging.getLogger("scalper.kill_switch")

_errors_lock = threading.Lock()
_exchange_error_ts: deque[float] = deque(maxlen=200)


def record_exchange_failure() -> None:
    with _errors_lock:
        _exchange_error_ts.append(time.time())


def _errors_in_window(sec: float) -> int:
    now = time.time()
    with _errors_lock:
        return sum(1 for t in _exchange_error_ts if now - t <= sec)


@dataclass
class KillSwitchResult:
    triggered: bool
    reason: str
    details: dict[str, Any]


def evaluate_kill_switch(
    *,
    db: Session,
    st: BotSettings,
    paper: bool,
    drawdown_pct: float,
    equity: float,
    atr_spike_ratio: float | None = None,
) -> KillSwitchResult:
    """
    Проверка условий kill switch. При срабатывании — set_pause(..., kill_switch).
    paper: только лог/метрики опционально, не паузим paper если не нужно — паузим оба для единообразия.
    """
    s = get_settings()
    if not s.kill_switch_enabled:
        return KillSwitchResult(False, "", {})

    details: dict[str, Any] = {}

    dd_lim = float(s.kill_switch_drawdown_pct)
    if dd_lim > 0 and drawdown_pct >= dd_lim:
        details["drawdown_pct"] = drawdown_pct
        _trigger("drawdown", f"drawdown>={dd_lim}% actual={drawdown_pct:.2f}%", details)
        return KillSwitchResult(True, "drawdown", details)

    err_n = int(s.kill_switch_exchange_errors_window_n)
    err_sec = float(s.kill_switch_exchange_errors_window_sec)
    if err_n > 0 and err_sec > 0:
        n = _errors_in_window(err_sec)
        if n >= err_n:
            details["errors_in_window"] = n
            _trigger("exchange_errors", f"exchange_errors {n}>={err_n} in {err_sec}s", details)
            return KillSwitchResult(True, "exchange_errors", details)

    if (
        atr_spike_ratio is not None
        and float(s.kill_switch_atr_spike_ratio) > 0
        and atr_spike_ratio >= float(s.kill_switch_atr_spike_ratio)
    ):
        details["atr_spike_ratio"] = atr_spike_ratio
        _trigger("volatility_spike", f"atr_spike ratio={atr_spike_ratio:.2f}", details)
        return KillSwitchResult(True, "volatility_spike", details)

    min_eq = float(s.kill_switch_min_equity_usdt)
    if min_eq > 0 and equity < min_eq:
        details["equity"] = equity
        _trigger("low_equity", f"equity_below_floor {equity}<{min_eq}", details)
        return KillSwitchResult(True, "low_equity", details)

    return KillSwitchResult(False, "", {})


def _trigger(reason_label: str, reason_detail: str, details: dict[str, Any]) -> None:
    cur = load_control()
    if cur.get("paused") and cur.get("source") == "kill_switch":
        return
    set_pause(f"KILL_SWITCH: {reason_detail}", source="kill_switch")
    try:
        kill_switch_triggered_total.labels(reason=reason_label).inc()
    except Exception:
        pass
    try:
        system_health_gauge.set(0.0)
    except Exception:
        pass
    emit(E_KILL_SWITCH, {"reason": reason_label, "detail": reason_detail, "details": details})
    send_alert("Kill switch", reason_detail, level="error")
    try:
        sentry_sdk.capture_message(f"KILL_SWITCH {reason_label}: {reason_detail}", level="error")
    except Exception:
        pass
    logger.error("kill_switch %s %s", reason_label, reason_detail)


def kill_switch_active() -> bool:
    c = load_control()
    return bool(c.get("paused") and c.get("source") == "kill_switch")


def clear_kill_switch_if_manual() -> None:
    """Снятие только вручную через trading resume API (не вызывать из бота)."""
    pass


def recent_equity_atr_spike_ratio(db: Session, st: BotSettings) -> float | None:
    """Отношение краткой волатильности equity к длинной (прокси всплеска риска)."""
    from sqlalchemy import desc

    mode = "paper" if st.paper_mode else "live"
    rows = (
        db.query(EquityPoint.equity)
        .filter(EquityPoint.mode == mode)
        .order_by(desc(EquityPoint.ts))
        .limit(30)
        .all()
    )
    if len(rows) < 15:
        return None
    eqs = [float(r[0]) for r in reversed(rows)]
    rets = [abs((eqs[i] - eqs[i - 1]) / max(eqs[i - 1], 1e-9)) for i in range(1, len(eqs))]
    if len(rets) < 10:
        return None
    import statistics

    short = statistics.pstdev(rets[-5:])
    long = statistics.pstdev(rets[:-5]) or 1e-9
    return float(short / long)
