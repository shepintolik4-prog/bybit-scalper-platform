"""Пауза торговли (smart / ручная) — JSON store."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import verify_api_secret
from app.services.trading_control_store import clear_pause, load_control, set_pause

router = APIRouter(prefix="/api/trading", tags=["trading_control"])


@router.get("/control")
def get_trading_control():
    return load_control()


class PauseBody(BaseModel):
    reason: str = "manual_pause"


@router.post("/pause")
def post_pause(body: PauseBody, _: None = Depends(verify_api_secret)):
    set_pause(body.reason or "manual_pause", source="manual_api")
    return load_control()


@router.post("/resume")
def post_resume(_: None = Depends(verify_api_secret)):
    clear_pause()
    return load_control()
