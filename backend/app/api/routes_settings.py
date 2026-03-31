from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import verify_api_secret
from app.config import get_settings


_RUNTIME_HIDE: frozenset[str] = frozenset(
    {
        "bybit_api_key",
        "bybit_api_secret",
        "api_secret",
        "secret_key",
        "database_url",
        "sentry_dsn_backend",
        "alert_telegram_bot_token",
        "alert_telegram_chat_id",
        "cors_origins",
        "firecrawl_api_key",
        "hf_token",
        "langfuse_secret_key",
        "browserbase_api_key",
        "browserbase_ws_endpoint",
    }
)


def _bot_runtime_safe() -> dict[str, Any]:
    s = get_settings()
    return {k: v for k, v in s.model_dump().items() if k not in _RUNTIME_HIDE}


from app.database import get_db
from app.models.orm import BotSettings
from app.schemas.dto import RealModeConfirm, SettingsIn, SettingsOut

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _row(db: Session) -> BotSettings:
    row = db.query(BotSettings).filter_by(id=1).first()
    if not row:
        row = BotSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("", response_model=SettingsOut)
def get_settings_api(db: Session = Depends(get_db)) -> SettingsOut:
    s = get_settings()
    r = _row(db)
    return SettingsOut(
        paper_mode=r.paper_mode,
        bot_enabled=r.bot_enabled,
        leverage=r.leverage,
        risk_per_trade_pct=r.risk_per_trade_pct,
        max_drawdown_pct=r.max_drawdown_pct,
        max_open_positions=r.max_open_positions,
        virtual_balance=r.virtual_balance,
        real_available=bool(s.confirm_real_trading),
        bot_runtime=_bot_runtime_safe(),
    )


@router.patch("", response_model=SettingsOut)
def patch_settings(
    body: SettingsIn,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_secret),
) -> SettingsOut:
    r = _row(db)
    data = body.model_dump(exclude_unset=True)
    if data.get("paper_mode") is False:
        raise HTTPException(
            status_code=400,
            detail="Для перехода в реальный режим используйте POST /api/settings/real-mode",
        )
    for k, v in data.items():
        setattr(r, k, v)
    db.commit()
    db.refresh(r)
    s = get_settings()
    return SettingsOut(
        paper_mode=r.paper_mode,
        bot_enabled=r.bot_enabled,
        leverage=r.leverage,
        risk_per_trade_pct=r.risk_per_trade_pct,
        max_drawdown_pct=r.max_drawdown_pct,
        max_open_positions=r.max_open_positions,
        virtual_balance=r.virtual_balance,
        real_available=bool(s.confirm_real_trading),
        bot_runtime=_bot_runtime_safe(),
    )


@router.post("/real-mode", response_model=SettingsOut)
def enable_real_mode(body: RealModeConfirm, db: Session = Depends(get_db), _: None = Depends(verify_api_secret)) -> SettingsOut:
    s = get_settings()
    if not s.confirm_real_trading:
        raise HTTPException(status_code=403, detail="CONFIRM_REAL_TRADING не включён в окружении сервера")
    if not body.acknowledge_risks:
        raise HTTPException(status_code=400, detail="Требуется подтверждение рисков (acknowledge_risks=true)")
    if body.confirm_phrase != "ENABLE_LIVE":
        raise HTTPException(status_code=400, detail="Неверная фраза подтверждения")
    r = _row(db)
    r.paper_mode = False
    r.real_mode_confirmed_at = datetime.utcnow()
    db.commit()
    db.refresh(r)
    return SettingsOut(
        paper_mode=r.paper_mode,
        bot_enabled=r.bot_enabled,
        leverage=r.leverage,
        risk_per_trade_pct=r.risk_per_trade_pct,
        max_drawdown_pct=r.max_drawdown_pct,
        max_open_positions=r.max_open_positions,
        virtual_balance=r.virtual_balance,
        real_available=True,
        bot_runtime=_bot_runtime_safe(),
    )
