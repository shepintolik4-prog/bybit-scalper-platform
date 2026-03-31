"""Сводка по стратегиям и ручное включение отключённых."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import verify_api_secret
from app.services.strategy_performance import force_enable_strategy, summary_for_api

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.get("/summary")
def strategies_summary():
    return summary_for_api()


class EnableBody(BaseModel):
    strategy_id: str


@router.post("/enable")
def strategies_enable(body: EnableBody, _: None = Depends(verify_api_secret)):
    if not body.strategy_id.strip():
        raise HTTPException(400, "strategy_id required")
    force_enable_strategy(body.strategy_id.strip())
    return {"ok": True, "strategy_id": body.strategy_id.strip()}
