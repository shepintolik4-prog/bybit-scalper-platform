from __future__ import annotations

import logging
import time
from typing import Any

from app.services.alerts import send_alert
from app.services.event_bus import (
    E_CONSISTENCY_ERROR,
    E_INVALID_TRANSITION,
    E_KILL_SWITCH,
    E_ORDER_CREATED,
    E_ORDER_FILLED,
    E_POSITION_CLOSED,
    subscribe,
)

logger = logging.getLogger("scalper.telegram.event_alerts")

_installed = False
_last_sent_ts: dict[str, float] = {}


def _rate_limit(key: str, *, min_interval_sec: float) -> bool:
    now = time.time()
    last = float(_last_sent_ts.get(key) or 0.0)
    if now - last < float(min_interval_sec):
        return True
    _last_sent_ts[key] = now
    return False


def install_telegram_event_alerts() -> None:
    """
    Hooks event_bus -> send_alert (Telegram + local log).
    Safe to call multiple times.
    """
    global _installed
    if _installed:
        return
    _installed = True

    def on_order_filled(ev: dict[str, Any]) -> None:
        if _rate_limit("order_filled", min_interval_sec=1.0):
            return
        sym = ev.get("symbol")
        side = ev.get("side")
        mode = ev.get("mode")
        trade_id = ev.get("trade_id")
        send_alert(
            "Trade opened",
            f"{sym} {side} ({mode})",
            level="info",
            extra={"trade_id": trade_id},
        )

    def on_position_closed(ev: dict[str, Any]) -> None:
        if _rate_limit("position_closed", min_interval_sec=1.0):
            return
        sym = ev.get("symbol")
        mode = ev.get("mode")
        pnl = ev.get("pnl_usdt")
        reason = ev.get("reason")
        trade_id = ev.get("trade_id")
        send_alert(
            "Trade closed",
            f"{sym} ({mode}) pnl={round(float(pnl or 0.0), 4)} USDT reason={reason}",
            level="info",
            extra={"trade_id": trade_id},
        )

    def on_consistency_error(ev: dict[str, Any]) -> None:
        if _rate_limit("consistency_error", min_interval_sec=30.0):
            return
        issues = ev.get("issues") or []
        body = ";\n".join([str(x) for x in issues[:10]]) if issues else "unknown"
        send_alert("Consistency error", body, level="warning")

    def on_kill_switch(ev: dict[str, Any]) -> None:
        if _rate_limit("kill_switch", min_interval_sec=10.0):
            return
        send_alert("Kill-switch", "Trading paused by kill-switch", level="error", extra=ev)

    def on_invalid_transition(ev: dict[str, Any]) -> None:
        if _rate_limit("invalid_transition", min_interval_sec=10.0):
            return
        send_alert("Invalid transition", "State machine error", level="warning", extra=ev)

    # Low-level events (optional): useful for live-debugging, but can be noisy.
    def on_order_created(ev: dict[str, Any]) -> None:
        if _rate_limit("order_created", min_interval_sec=2.0):
            return
        send_alert("Order created", f"{ev.get('symbol')} ({ev.get('mode')})", level="info", extra=ev)

    subscribe(E_ORDER_FILLED, on_order_filled)
    subscribe(E_POSITION_CLOSED, on_position_closed)
    subscribe(E_CONSISTENCY_ERROR, on_consistency_error)
    subscribe(E_KILL_SWITCH, on_kill_switch)
    subscribe(E_INVALID_TRANSITION, on_invalid_transition)
    subscribe(E_ORDER_CREATED, on_order_created)

    logger.info("telegram event alerts installed")

