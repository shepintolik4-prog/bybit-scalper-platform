"""Фоновое переобучение XGBoost по расписанию."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_settings
from app.monitoring.prometheus_metrics import retrain_runs

logger = logging.getLogger(__name__)


def _pick_retrain_symbol() -> str:
    s = get_settings()
    if not s.scan_all_usdt_perpetual:
        return s.symbol_list[0] if s.symbol_list else "BTC/USDT:USDT"
    from sqlalchemy import func

    from app.database import SessionLocal
    from app.models.orm import TradeRecord

    db = SessionLocal()
    try:
        row = (
            db.query(TradeRecord.symbol, func.count().label("c"))
            .filter(TradeRecord.status == "closed")
            .group_by(TradeRecord.symbol)
            .order_by(func.count().desc())
            .first()
        )
        if row and row[0]:
            return str(row[0])
    finally:
        db.close()
    return s.symbol_list[0] if s.symbol_list else "BTC/USDT:USDT"


def _job() -> None:
    s = get_settings()
    if not s.auto_retrain_enabled:
        return
    sym = _pick_retrain_symbol()
    try:
        from app.services.backtest import train_and_save
        from app.ml.predictor import get_predictor

        path = train_and_save(sym, s.model_dir)
        get_predictor().reload()
        retrain_runs.labels(symbol=sym).inc()
        logger.info("auto_retrain_ok symbol=%s path=%s", sym, path)
    except Exception as e:
        logger.exception("auto_retrain_failed: %s", e)


def register_retrain_jobs(scheduler: BackgroundScheduler) -> None:
    s = get_settings()
    if not s.auto_retrain_enabled:
        return
    scheduler.add_job(
        _job,
        "interval",
        hours=float(s.auto_retrain_interval_hours),
        id="auto_retrain_xgb",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("auto_retrain scheduled every %s h", s.auto_retrain_interval_hours)
