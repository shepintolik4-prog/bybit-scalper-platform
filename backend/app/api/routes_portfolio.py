"""
HTTP-обёртка над portfolio_manager: оценка весов по рыночным доходностям (multi-asset).
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import verify_api_secret
from app.config import get_settings
from app.services import bybit_exchange
from app.services.portfolio_manager import (
    AllocationMethod,
    PortfolioConstraints,
    PortfolioManager,
    allocation_to_dict,
)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class PortfolioAllocateRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=2, description="USDT perpetual ccxt-символы")
    equity_usdt: float = Field(gt=0)
    lookback: int = Field(300, ge=80, le=2000)
    timeframe: str = Field("5m", description="Таймфрейм OHLCV ccxt")
    method: str = Field("risk_parity_erc")
    portfolio_fraction: float = Field(1.0, ge=0.05, le=1.0)
    signal_scores: dict[str, float] | None = Field(
        None,
        description="Опционально: сила сигнала по символу для dynamic tilt",
    )
    w_max: float = Field(0.45, ge=0.05, le=1.0)
    max_pair_correlation: float = Field(0.85, ge=0.5, le=0.99)


def _returns_matrix(symbols: list[str], timeframe: str, limit: int) -> pd.DataFrame:
    cols: dict[str, pd.Series] = {}
    for sym in symbols:
        try:
            raw = bybit_exchange.fetch_ohlcv(sym, timeframe, limit)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"{sym}: {e}") from e
        if len(raw) < 40:
            raise HTTPException(status_code=400, detail=f"{sym}: мало баров ({len(raw)})")
        df = pd.DataFrame(raw, columns=["ts", "o", "h", "l", "c", "v"])
        cols[sym] = df["c"].astype(float).pct_change()
    out = pd.DataFrame(cols).dropna()
    if out.shape[0] < 40:
        raise HTTPException(status_code=400, detail="Недостаточно совместных наблюдений после выравнивания")
    return out


def _parse_method(name: str) -> AllocationMethod:
    key = name.strip().lower()
    for m in AllocationMethod:
        if m.value == key:
            return m
    raise HTTPException(status_code=400, detail=f"Неизвестный method: {name}")


@router.post("/allocate")
def allocate_portfolio(
    body: PortfolioAllocateRequest,
    _: None = Depends(verify_api_secret),
) -> dict[str, Any]:
    settings = get_settings()
    rets = _returns_matrix(body.symbols, body.timeframe, body.lookback)
    method = _parse_method(body.method)
    cons = PortfolioConstraints(
        w_max=body.w_max,
        max_pair_correlation=body.max_pair_correlation,
    )
    pm = PortfolioManager(constraints=cons)
    sig = None
    if body.signal_scores:
        sig = pd.Series(body.signal_scores)
    try:
        result = pm.allocate_capital(
            body.equity_usdt,
            rets,
            method=method,
            signal_scores=sig,
            portfolio_fraction=body.portfolio_fraction,
            periods_per_year=settings.portfolio_periods_per_year,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    out = allocation_to_dict(result)
    out["lookback_rows"] = int(rets.shape[0])
    return out


@router.get("/methods")
def list_methods() -> dict[str, list[str]]:
    return {"methods": [m.value for m in AllocationMethod]}
