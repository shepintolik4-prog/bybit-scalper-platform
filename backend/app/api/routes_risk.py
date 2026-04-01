"""
Метрики риска в стиле PM / quant-fund (концентрация, кластеры, лимиты).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.exchange.bybit_client import BybitClient
from app.models.orm import BotSettings, Position
from app.services.fund_risk import compute_fund_snapshot

router = APIRouter(prefix="/api/risk", tags=["risk"])
_BYBIT = BybitClient()


def _equity_usdt(db: Session, st: BotSettings) -> float:
    if st.paper_mode:
        locked = sum(float(p.size_usdt) for p in db.query(Position).filter(Position.mode == "paper").all())
        return float(st.virtual_balance) + locked
    try:
        ex = _BYBIT.create_trading_exchange()
        bal = ex.fetch_balance()
        return float(bal["USDT"]["total"] or 0)
    except Exception:
        return 0.0


@router.get("/fund")
def get_fund_risk_snapshot(db: Session = Depends(get_db)) -> dict:
    st = db.query(BotSettings).filter_by(id=1).first()
    if not st:
        return {"error": "no_settings"}
    equity = _equity_usdt(db, st)
    mode = "paper" if st.paper_mode else "live"
    positions = db.query(Position).filter(Position.mode == mode).all()
    triples = [(p.symbol, float(p.size_usdt), int(p.leverage)) for p in positions]
    out = compute_fund_snapshot(equity, triples)
    out["paper_mode"] = st.paper_mode
    return out
