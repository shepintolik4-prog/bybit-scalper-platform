import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import EquityPoint, Position, TradeRecord
from app.schemas.dto import EquityPointOut, ForceCloseBySymbolIn, PositionOut, TradeOut
from app.services.performance_analyzer import analyze_performance

router = APIRouter(tags=["data"])


def _parse_expl(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


def _mark_for_position(r: Position) -> float | None:
    m = getattr(r, "last_mark_price", None)
    if m is not None and float(m) > 0:
        return float(m)
    # Важно: /api/positions дергается панелью часто. Сетевые запросы за mark/ohlcv
    # здесь приводят к долгим ответам, 499 в nginx и "пустой" UI (Promise.all ждёт).
    # Поэтому в API позиций используем только то, что движок уже положил в БД.
    return None


def _pos_metrics(
    r: Position, mark: float | None
) -> tuple[float | None, float | None, float | None]:
    if mark is None or mark <= 0:
        return None, None, None
    e = float(r.entry_price)
    lev = max(int(r.leverage), 1)
    sz = float(r.size_usdt)
    sl = float(r.stop_loss)
    tp = float(r.take_profit)
    sd = (r.side or "").lower().strip()
    if sd in ("buy", "long"):
        unreal = (mark - e) / e * sz * lev
        # long: TP выше mark; SL ниже mark
        pct_tp = 0.0 if mark >= tp else (tp - mark) / mark * 100.0
        pct_sl = 0.0 if mark <= sl else (mark - sl) / mark * 100.0
    else:
        unreal = (e - mark) / e * sz * lev
        # short: TP ниже mark; SL выше mark
        pct_tp = 0.0 if mark <= tp else (mark - tp) / mark * 100.0
        pct_sl = 0.0 if mark >= sl else (sl - mark) / mark * 100.0
    return (
        round(unreal, 4),
        round(pct_tp, 3),
        round(pct_sl, 3),
    )


@router.get("/api/trades", response_model=list[TradeOut])
def list_trades(
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
) -> list[TradeOut]:
    rows = db.query(TradeRecord).order_by(TradeRecord.opened_at.desc()).limit(limit).all()
    out: list[TradeOut] = []
    for r in rows:
        out.append(
            TradeOut(
                id=r.id,
                symbol=r.symbol,
                side=r.side,
                entry_price=r.entry_price,
                exit_price=r.exit_price,
                size_usdt=r.size_usdt,
                pnl_usdt=r.pnl_usdt,
                pnl_pct=r.pnl_pct,
                opened_at=r.opened_at,
                closed_at=r.closed_at,
                explanation=_parse_expl(r.explanation_json),
                mode=r.mode,
                status=r.status,
                exchange_order_id=getattr(r, "exchange_order_id", None),
                client_order_id=getattr(r, "client_order_id", None),
                order_status=getattr(r, "order_status", None) or "unknown",
                filled_contracts=float(getattr(r, "filled_contracts", 0) or 0),
                data_source=getattr(r, "data_source", None) or "db",
                lifecycle_state=getattr(r, "lifecycle_state", None) or "filled",
            )
        )
    return out


@router.get("/api/positions", response_model=list[PositionOut])
def list_positions(
    mode: str | None = Query(None, description="Фильтр: paper | live; без параметра — все"),
    db: Session = Depends(get_db),
) -> list[PositionOut]:
    q = db.query(Position)
    if mode in ("paper", "live"):
        q = q.filter(Position.mode == mode)
    rows = q.all()
    out: list[PositionOut] = []
    for r in rows:
        mark = _mark_for_position(r)
        unreal, ptp, psl = _pos_metrics(r, mark)
        last_out = mark if mark is not None else getattr(r, "last_mark_price", None)
        out.append(
            PositionOut(
                id=r.id,
                symbol=r.symbol,
                side=r.side,
                entry_price=r.entry_price,
                size_usdt=r.size_usdt,
                leverage=r.leverage,
                stop_loss=r.stop_loss,
                take_profit=r.take_profit,
                opened_at=r.opened_at,
                explanation=_parse_expl(r.explanation_json),
                mode=r.mode,
                exchange_order_id=getattr(r, "exchange_order_id", None),
                client_order_id=getattr(r, "client_order_id", None),
                contracts_qty=getattr(r, "contracts_qty", None),
                data_source=getattr(r, "data_source", None) or "db",
                last_mark_price=float(last_out) if last_out is not None else None,
                lifecycle_state=getattr(r, "lifecycle_state", None) or "filled",
                unrealized_pnl_usdt=unreal,
                pct_to_take_profit=ptp,
                pct_to_stop_loss=psl,
            )
        )
    return out


@router.post("/api/positions/force-close")
def force_close_position_by_symbol(body: ForceCloseBySymbolIn, db: Session = Depends(get_db)) -> dict:
    """
    Принудительно закрыть позицию по символу (paper/live). При двух режимах на один символ укажите mode.
    """
    from app.engine.trading_engine import engine as bot_engine

    result = bot_engine.force_close_position_by_symbol(
        db,
        body.symbol,
        mode=body.mode,
        confirm_db_without_exchange=body.confirm_db_without_exchange,
    )
    if result.get("ok"):
        return result
    err = str(result.get("error", "failed"))
    if err == "position_not_found":
        raise HTTPException(status_code=404, detail=result)
    if err == "ambiguous_symbol_pass_mode":
        raise HTTPException(status_code=409, detail=result)
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/positions/{position_id}/force-close")
def force_close_paper_position(position_id: int, db: Session = Depends(get_db)) -> dict:
    """
    Принудительно закрыть paper-позицию по id (тот же путь, что TP/SL в движке).
    Нужен при залипании строки в UI или блокировке капа по открытым позициям.
    """
    from app.services.bot_engine import engine as bot_engine

    result = bot_engine.force_close_paper_position(db, position_id)
    if result.get("ok"):
        return result
    err = str(result.get("error", "failed"))
    if err == "position_not_found":
        raise HTTPException(status_code=404, detail=err)
    raise HTTPException(status_code=400, detail=err)


@router.get("/api/performance/analyze")
def api_analyze_performance(db: Session = Depends(get_db)) -> dict:
    """Сводка по strategy_id и symbol (закрытые сделки в БД) + strategy_stats.json."""
    return analyze_performance(db)


@router.get("/api/equity", response_model=list[EquityPointOut])
def list_equity(limit: int = Query(500, le=5000), db: Session = Depends(get_db)) -> list[EquityPointOut]:
    rows = db.query(EquityPoint).order_by(EquityPoint.ts.desc()).limit(limit).all()
    return [
        EquityPointOut(ts=r.ts, equity=r.equity, balance=r.balance, mode=r.mode)
        for r in reversed(rows)
    ]
