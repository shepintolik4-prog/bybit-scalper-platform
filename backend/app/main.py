from contextlib import asynccontextmanager
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.routes_bot import router as bot_router
from app.api.routes_data import router as data_router
from app.api.routes_ml import router as ml_router
from app.api.routes_portfolio import router as portfolio_router
from app.api.routes_autonomy import router as autonomy_router
from app.api.routes_health import router as health_extra_router
from app.api.routes_system import router as system_router
from app.api.routes_scan import router as scan_router
from app.api.routes_strategies import router as strategies_router
from app.api.routes_trading_control import router as trading_control_router
from app.api.routes_debug import router as debug_router
from app.api.routes_keys import router as keys_router
from app.api.routes_risk import router as risk_router
from app.api.routes_settings import router as settings_router
from app.config import get_settings
from app.database import Base, engine
from app.database_migrations import run_execution_schema_migrations
from app.logging_config import setup_logging
from app.models.orm import BotSettings, BybitApiCredentials  # noqa: F401 — таблица в metadata
from app.monitoring.middleware import MetricsMiddleware
from app.monitoring.prometheus_metrics import metrics_response, refresh_strategy_gauges
from app.monitoring.sentry_setup import init_sentry
from app.services.bot_engine import engine as bot_engine
from app.services.retrain_scheduler import register_retrain_jobs
from app.services.startup_self_check import run_full_system_check
from app.telegram.event_alerts import install_telegram_event_alerts

_retrain_scheduler: BackgroundScheduler | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _retrain_scheduler
    settings = get_settings()
    setup_logging(settings.log_level)
    init_sentry()
    Base.metadata.create_all(bind=engine)
    run_execution_schema_migrations(engine)
    from sqlalchemy.orm import sessionmaker

    S = sessionmaker(bind=engine)
    db = S()
    try:
        if not db.query(BotSettings).filter_by(id=1).first():
            db.add(
                BotSettings(
                    id=1,
                    paper_mode=True,
                    bot_enabled=False,
                    leverage=settings.default_leverage,
                    risk_per_trade_pct=settings.risk_per_trade_pct,
                    max_drawdown_pct=settings.max_drawdown_pct,
                    max_open_positions=settings.max_open_positions,
                    virtual_balance=settings.paper_initial_balance,
                )
            )
            db.commit()
        row = db.query(BotSettings).filter_by(id=1).first()
        if row and settings.full_aggressive_max_flow and settings.full_aggressive_auto_enable_bot:
            row.bot_enabled = True
            row.max_open_positions = max(int(row.max_open_positions), int(settings.full_aggressive_max_positions))
            row.risk_per_trade_pct = float(settings.full_aggressive_risk_pct)
            row.leverage = min(
                max(int(row.leverage), int(settings.full_aggressive_min_leverage)),
                int(settings.full_aggressive_max_leverage),
            )
            db.commit()
        if row and settings.debug and not row.bot_enabled and row.paper_mode:
            row.bot_enabled = True
            db.commit()
            logging.getLogger("scalper").warning(
                "debug_auto_enable_bot paper_mode=True (DEBUG=true в .env)"
            )
    finally:
        db.close()
    _retrain_scheduler = BackgroundScheduler()
    register_retrain_jobs(_retrain_scheduler)
    _retrain_scheduler.start()
    try:
        install_telegram_event_alerts()
    except Exception:
        logging.getLogger("scalper").exception("telegram_event_alerts_install_failed")
    bot_engine.ensure_worker()
    try:
        refresh_strategy_gauges()
    except Exception:
        pass
    _log = logging.getLogger("scalper")
    try:
        sc = run_full_system_check()
        _log.info("startup_self_check status=%s checks=%s", sc.get("status"), sc.get("checks", {}).keys())
    except Exception:
        _log.exception("startup_self_check_failed")
    S2 = sessionmaker(bind=engine)
    db_rec = S2()
    try:
        rec = bot_engine.reconcile_stuck_positions_on_startup(db_rec)
        stale = bot_engine.reconcile_long_running_paper_on_startup(db_rec)
        db_rec.commit()
        if rec.get("closing") or rec.get("closed_ok") or rec.get("reverted_filled") or rec.get("errors"):
            _log.info("position_startup_reconcile %s", rec)
        if stale.get("closed") or stale.get("errors"):
            _log.info("position_startup_stale_paper %s", stale)
    except Exception:
        db_rec.rollback()
        _log.exception("position_startup_reconcile_failed")
    finally:
        db_rec.close()
    yield
    if _retrain_scheduler:
        _retrain_scheduler.shutdown(wait=False)
    bot_engine.stop()


app = FastAPI(title="Bybit Scalper ML", lifespan=lifespan)

settings = get_settings()
app.add_middleware(MetricsMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(settings_router)
app.include_router(bot_router)
app.include_router(data_router)
app.include_router(ml_router)
app.include_router(portfolio_router)
app.include_router(risk_router)
app.include_router(keys_router)
app.include_router(autonomy_router)
app.include_router(scan_router)
app.include_router(health_extra_router)
app.include_router(system_router)
app.include_router(strategies_router)
app.include_router(trading_control_router)
app.include_router(debug_router)


@app.get("/", include_in_schema=False)
def root():
    """Корень без UI: откройте /docs или фронт на :5173."""
    return {
        "service": "bybit-scalper-platform API",
        "docs": "/docs",
        "health": "/api/health",
        "hint": "Веб-интерфейс: npm run dev во frontend (обычно http://127.0.0.1:5173)",
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "env": settings.sentry_environment}


@app.get("/api/health/ready")
def ready():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "not_ready", "db": str(e)})
    return {"status": "ready", "db": "ok"}


@app.get(settings.prometheus_metrics_path, include_in_schema=False)
def prometheus_metrics():
    return metrics_response()
