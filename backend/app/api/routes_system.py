"""Сводка состояния production-engine: health, kill switch, circuit breaker, consistency."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.orm import BotSettings
from app.schemas.dto import SystemStatusOut
from app.services.consistency_checks import get_last_consistency_snapshot
from app.services.kill_switch import kill_switch_active
from app.services.risk_guards import global_guards
from app.services.trading_control_store import load_control

router = APIRouter(tags=["system"])


@router.get("/api/system/status", response_model=SystemStatusOut)
def system_status(db: Session = Depends(get_db)) -> SystemStatusOut:
    s = get_settings()
    st = db.query(BotSettings).filter_by(id=1).first()
    ctrl = load_control()
    g = global_guards()
    ks = kill_switch_active()
    paused = bool(ctrl.get("paused"))
    circuit_blocks = not g.circuit_allows_new_trades()

    health = "OK"
    if ks or (paused and ctrl.get("source") == "kill_switch"):
        health = "STOPPED"
    elif paused or circuit_blocks:
        health = "WARNING"

    cons = get_last_consistency_snapshot()
    return SystemStatusOut(
        health=health,
        kill_switch_active=ks,
        trading_paused=paused,
        pause_reason=(ctrl.get("reason") or "")[:500] if paused else None,
        pause_source=str(ctrl.get("source") or "") if paused else None,
        circuit_breaker_open=circuit_blocks,
        consistency_last_ok=bool(cons.get("ok", True)) if cons else True,
        consistency_issues=list(cons.get("issues") or [])[:15],
        bot_enabled=bool(st.bot_enabled) if st else False,
        paper_mode=bool(st.paper_mode) if st else True,
        settings_flags={
            "kill_switch_enabled": s.kill_switch_enabled,
            "consistency_checks_enabled": s.consistency_checks_enabled,
            "max_trades_per_minute": s.max_trades_per_minute,
        },
    )
