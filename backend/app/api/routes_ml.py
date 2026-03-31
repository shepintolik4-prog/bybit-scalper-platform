from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import verify_api_secret
from app.config import get_settings
from app.ml.meta_filter import get_meta_filter
from app.ml.predictor import get_predictor
from app.schemas.dto import BacktestResult, RealisticBacktestOut
from app.services.backtest import (
    run_backtest,
    run_realistic_backtest,
    train_and_save,
    train_meta_and_save,
)

router = APIRouter(prefix="/api/ml", tags=["ml"])


@router.post("/backtest", response_model=BacktestResult)
def backtest(
    symbol: str = Query(..., description="Например BTC/USDT:USDT"),
    _: None = Depends(verify_api_secret),
):
    try:
        s = run_backtest(symbol)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return BacktestResult(
        symbol=s.symbol,
        trades=s.trades,
        winrate=s.winrate,
        pnl_pct=s.pnl_pct,
        max_dd_pct=s.max_dd_pct,
        model_accuracy=s.model_accuracy,
    )


@router.post("/backtest/realistic", response_model=RealisticBacktestOut)
def backtest_realistic(
    symbol: str = Query(...),
    _: None = Depends(verify_api_secret),
):
    try:
        s = run_realistic_backtest(symbol)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return RealisticBacktestOut(
        symbol=s.symbol,
        trades=s.trades,
        winrate=s.winrate,
        pnl_pct=s.pnl_pct,
        max_dd_pct=s.max_dd_pct,
        wf_windows=s.wf_windows,
        avg_accuracy=s.avg_accuracy,
    )


@router.post("/train")
def train(
    symbol: str = Query("BTC/USDT:USDT"),
    _: None = Depends(verify_api_secret),
):
    settings = get_settings()
    path = train_and_save(symbol, settings.model_dir)
    get_predictor().reload()
    return {"saved": path, "symbol": symbol}


@router.post("/train/meta")
def train_meta(
    symbol: str = Query("BTC/USDT:USDT"),
    _: None = Depends(verify_api_secret),
):
    settings = get_settings()
    try:
        path = train_meta_and_save(symbol, settings.model_dir)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    get_meta_filter().reload()
    return {"saved": path, "symbol": symbol}
