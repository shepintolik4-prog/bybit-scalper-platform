from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import verify_api_secret
from app.database import get_db
from app.models.orm import BotSettings
from app.schemas.dto import BotStatusOut
from app.services.bot_engine import engine

router = APIRouter(prefix="/api/bot", tags=["bot"])


def _row(db: Session) -> BotSettings:
    row = db.query(BotSettings).filter_by(id=1).first()
    if not row:
        row = BotSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.post("/start", response_model=BotStatusOut)
def start_bot(
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_secret),
) -> BotStatusOut:
    r = _row(db)
    r.bot_enabled = True
    db.commit()
    engine.ensure_worker()
    return BotStatusOut(
        running=True,
        paper_mode=r.paper_mode,
        bot_enabled=True,
        message="Бот включён (воркер в фоне)",
    )


@router.post("/stop", response_model=BotStatusOut)
def stop_bot(
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_secret),
) -> BotStatusOut:
    r = _row(db)
    r.bot_enabled = False
    db.commit()
    return BotStatusOut(
        running=False,
        paper_mode=r.paper_mode,
        bot_enabled=False,
        message="Бот выключен",
    )


@router.get("/status", response_model=BotStatusOut)
def status_bot(db: Session = Depends(get_db)) -> BotStatusOut:
    r = _row(db)
    return BotStatusOut(
        running=bool(r.bot_enabled),
        paper_mode=r.paper_mode,
        bot_enabled=r.bot_enabled,
        message="OK",
    )
