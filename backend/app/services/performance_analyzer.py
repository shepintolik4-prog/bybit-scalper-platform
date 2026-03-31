"""
Агрегированная аналитика: по strategy_id и symbol (из закрытых сделок в БД).
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.models.orm import TradeRecord
from app.services.strategy_performance import load_stats, summary_for_api


def _strat_from_trade(t: TradeRecord) -> str:
    raw = t.explanation_json
    if not raw:
        return "unknown"
    try:
        d = json.loads(raw)
        return str(d.get("strategy_id") or "unknown")
    except Exception:
        return "unknown"


def analyze_performance(db: Session | None = None) -> dict[str, Any]:
    """
    Группировка закрытых сделок: winrate, avg/total pnl, max drawdown кумулятивного PnL.
    Плюс снимок файла strategy_stats.json.
    """
    base = summary_for_api()
    out: dict[str, Any] = {
        "strategy_stats_file": base,
        "by_strategy": {},
        "by_symbol": {},
        "totals": {"closed_trades": 0, "total_pnl_usdt": 0.0, "equity_curve_max_drawdown_usdt": 0.0},
    }
    if db is None:
        return out

    closed = (
        db.query(TradeRecord)
        .filter(TradeRecord.status == "closed", TradeRecord.pnl_usdt.isnot(None))
        .order_by(TradeRecord.closed_at.asc())
        .all()
    )

    agg_s: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"n": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
    )
    agg_sym: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"n": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
    )

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    total_pnl = 0.0
    for t in closed:
        pnl = float(t.pnl_usdt or 0)
        total_pnl += pnl
        cum += pnl
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

        sid = _strat_from_trade(t)
        sym = t.symbol
        for key, bucket in ((sid, agg_s), (sym, agg_sym)):
            b = bucket[key]
            b["n"] = int(b["n"]) + 1
            b["total_pnl"] = float(b["total_pnl"]) + pnl
            if pnl >= 0:
                b["wins"] = int(b["wins"]) + 1
            else:
                b["losses"] = int(b["losses"]) + 1

    def finalize(m: dict[str, dict[str, float | int]]) -> dict[str, Any]:
        r: dict[str, Any] = {}
        for k, v in m.items():
            n = int(v["n"])
            wins = int(v["wins"])
            losses = int(v["losses"])
            r[k] = {
                "trades": n,
                "wins": wins,
                "losses": losses,
                "winrate": round(wins / n, 4) if n else 0.0,
                "total_pnl_usdt": round(float(v["total_pnl"]), 4),
                "avg_pnl_usdt": round(float(v["total_pnl"]) / n, 6) if n else 0.0,
            }
        return r

    out["by_strategy"] = finalize(agg_s)
    out["by_symbol"] = finalize(agg_sym)
    out["totals"] = {
        "closed_trades": len(closed),
        "total_pnl_usdt": round(total_pnl, 4),
        "equity_curve_max_drawdown_usdt": round(max_dd, 4),
    }
    st = load_stats()
    out["boosted_strategies"] = list(st.get("boosted_strategies") or [])
    out["disabled_strategies"] = list(st.get("disabled") or [])
    return out
