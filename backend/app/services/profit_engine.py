"""
Оптимизация прибыли: динамический порог edge, sizing, диверсификация, метрики, адаптив.
Капитал в приоритете: усиление фильтров при просадке, сжатие размера.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.orm import EquityPoint, TradeRecord
from app.services.adaptive_state import load_adaptive, save_adaptive
from app.services.correlation_service import passes_correlation_gate
from app.services.strategy_performance import is_strategy_boosted, load_stats


def effective_min_edge(
    _db: Session,
    settings: Settings,
    base_need: float,
    drawdown_pct: float,
    strategy_id: str,
) -> float:
    """
    Повышаем требуемый |edge| при просадке; корректируем по истории стратегии.
    """
    knee = float(settings.profit_dd_edge_knee_pct)
    boost = float(settings.profit_dd_edge_boost_per_pct)
    mult = 1.0
    if drawdown_pct > knee:
        mult += (drawdown_pct - knee) * boost
    mult = min(mult, float(settings.profit_dd_edge_max_mult))

    st = load_stats()
    row = (st.get("strategies") or {}).get(strategy_id) or {}
    n = int(row.get("wins", 0)) + int(row.get("losses", 0))
    wr = float(row.get("winrate", 0.5))
    strat_mult = 1.0
    if n >= int(settings.profit_strategy_min_trades_for_tilt):
        if wr < float(settings.profit_strategy_weak_winrate):
            strat_mult = float(settings.profit_strategy_weak_edge_mult)
        elif wr > float(settings.profit_strategy_strong_winrate):
            strat_mult = float(settings.profit_strategy_strong_edge_mult)
    if settings.full_aggressive_max_flow and is_strategy_boosted(strategy_id):
        strat_mult *= float(settings.full_aggressive_boost_edge_mult)
    return float(base_need) * mult * strat_mult


def apply_profit_scaling(
    base_margin_usdt: float,
    confidence: float,
    atr_pct: float,
    drawdown_pct: float,
    strategy_id: str,
    equity: float,
) -> float:
    """Масштаб маржи: confidence↑, волатильность↓, просадка↓, сильная стратегия↑."""
    s = get_settings()
    g = float(s.profit_confidence_size_gamma)
    conf_f = 0.65 + g * max(0.0, min(1.0, confidence) - 0.5)

    ref = max(float(s.profit_atr_size_reference), 1e-8)
    vol_f = math.sqrt(ref / max(atr_pct, ref * 0.15))
    vol_f = max(float(s.profit_atr_size_floor), min(float(s.profit_atr_size_cap), vol_f))

    knee = float(s.profit_dd_size_knee_pct)
    if drawdown_pct > knee:
        dd_f = max(
            float(s.profit_dd_size_floor_mult),
            1.0 - (drawdown_pct - knee) * float(s.profit_dd_size_compress_per_pct),
        )
    else:
        dd_f = 1.0

    st = load_stats()
    row = (st.get("strategies") or {}).get(strategy_id) or {}
    n = int(row.get("wins", 0)) + int(row.get("losses", 0))
    wr = float(row.get("winrate", 0.5))
    sf = 1.0
    if n >= int(s.profit_strategy_min_trades_for_tilt):
        if wr >= float(s.profit_strategy_strong_winrate):
            sf = float(s.profit_strategy_strong_size_mult)
        elif wr <= float(s.profit_strategy_weak_winrate):
            sf = float(s.profit_strategy_weak_size_mult)

    out = float(base_margin_usdt) * conf_f * vol_f * dd_f * sf
    cap = equity * float(s.max_margin_pct_equity) * float(s.profit_max_margin_fraction_of_cap)
    return float(max(5.0, min(out, cap)))


def diversification_adjust_candidates(
    candidates: list[tuple[Any, ...]],
    open_syms: list[str],
    universe: list[str],
    settings: Settings,
) -> list[tuple[Any, ...]]:
    """
    Усиливает selection_score у символов с лучшей диверсификацией относительно открытых.
    candidates: (combined_adj, sym, expl, feats, last_mid, reg, snap)
    """
    if not settings.profit_diversification_enabled or not open_syms:
        return candidates
    bonus = float(settings.profit_diversity_score_bonus)
    out: list[tuple[Any, ...]] = []
    for tup in candidates:
        combined_adj, sym, expl, feats, last_mid, reg, snap = tup
        ok, _ = passes_correlation_gate(sym, open_syms, universe)
        base = float(expl.get("selection_score", abs(combined_adj)))
        adj = base * (1.0 + bonus) if ok else base * max(0.85, 1.0 - bonus * 0.5)
        ne = dict(expl)
        ne["selection_score"] = round(adj, 6)
        ne["diversification_adjustment"] = round(adj / max(base, 1e-12), 4)
        out.append((combined_adj, sym, ne, feats, last_mid, reg, snap))
    return out


def compute_performance_metrics(db: Session, *, mode: str, hours: int = 720) -> dict[str, Any]:
    """Sharpe по equity, winrate/expectancy по закрытым сделкам."""
    since = datetime.utcnow() - timedelta(hours=hours)
    trades = (
        db.query(TradeRecord)
        .filter(
            TradeRecord.mode == mode,
            TradeRecord.status == "closed",
            TradeRecord.closed_at.isnot(None),
            TradeRecord.closed_at >= since,
        )
        .all()
    )
    wins = [t for t in trades if (t.pnl_usdt or 0) > 0]
    losses = [t for t in trades if (t.pnl_usdt or 0) < 0]
    n = len(trades)
    wr = len(wins) / n if n else 0.0
    aw = statistics.mean([t.pnl_usdt or 0 for t in wins]) if wins else 0.0
    al = abs(statistics.mean([t.pnl_usdt or 0 for t in losses])) if losses else 0.0
    expectancy = (wr * aw - (1 - wr) * al) if n else 0.0

    rows = (
        db.query(EquityPoint.equity)
        .filter(EquityPoint.mode == mode, EquityPoint.ts >= since)
        .order_by(EquityPoint.ts)
        .limit(2000)
        .all()
    )
    eq = [float(r[0]) for r in rows]
    sharpe = 0.0
    if len(eq) >= 10:
        rets = [(eq[i] - eq[i - 1]) / max(eq[i - 1], 1e-9) for i in range(1, len(eq))]
        mu = statistics.mean(rets)
        sd = statistics.pstdev(rets) or 1e-9
        periods = 365.0 * 24.0 * 12.0
        sharpe = (mu / sd) * math.sqrt(periods) if sd > 0 else 0.0

    best_sid = ""
    best_wr = -1.0
    st = load_stats()
    for sid, row in (st.get("strategies") or {}).items():
        nn = int(row.get("wins", 0)) + int(row.get("losses", 0))
        if nn < 5:
            continue
        w = float(row.get("winrate", 0))
        if w > best_wr:
            best_wr = w
            best_sid = sid

    return {
        "window_hours": hours,
        "mode": mode,
        "closed_trades": n,
        "winrate": round(wr, 4),
        "avg_win_usdt": round(aw, 4),
        "avg_loss_usdt": round(al, 4),
        "expectancy_usdt": round(expectancy, 6),
        "sharpe_proxy_equity": round(sharpe, 4),
        "best_strategy_id": best_sid or None,
        "best_strategy_winrate": round(best_wr, 4) if best_sid else None,
    }


def apply_adaptive_learning(db: Session) -> None:
    """
    Дополнение к self_improve: мягкая коррекция min_confidence_floor в adaptive_state.
    """
    import time

    s = get_settings()
    if not s.profit_adaptive_learning_enabled:
        return
    state = load_adaptive()
    last_ts = state.get("profit_adaptive_last_ts_unix")
    if last_ts is not None and time.time() - float(last_ts) < float(s.profit_adaptive_interval_sec):
        return
    since = datetime.utcnow() - timedelta(hours=s.self_improve_window_hours)
    rows = (
        db.query(TradeRecord)
        .filter(
            TradeRecord.status == "closed",
            TradeRecord.closed_at.isnot(None),
            TradeRecord.closed_at >= since,
        )
        .all()
    )
    if len(rows) < s.self_improve_min_trades:
        return
    wins = sum(1 for t in rows if (t.pnl_usdt or 0) > 0)
    wr = wins / len(rows)
    floor = float(state.get("min_confidence_floor", s.min_model_confidence))
    step = float(s.profit_adaptive_confidence_step)
    if wr < s.self_improve_winrate_low:
        floor = min(s.profit_adaptive_confidence_max, floor + step)
    elif wr > s.self_improve_winrate_high:
        floor = max(s.profit_adaptive_confidence_min, floor - step * 0.5)
    state["min_confidence_floor"] = floor
    state["profit_adaptive_last_winrate"] = wr
    state["profit_adaptive_last_ts_unix"] = time.time()
    save_adaptive(state)


def effective_min_model_confidence(settings: Settings) -> float:
    """Нижняя граница confidence с учётом adaptive_state."""
    state = load_adaptive()
    floor = state.get("min_confidence_floor")
    if floor is not None:
        out = max(
            float(settings.profit_adaptive_confidence_min),
            min(float(floor), float(settings.profit_adaptive_confidence_max)),
        )
    else:
        out = float(settings.min_model_confidence)
    if settings.aggressive_mode:
        out = max(float(settings.profit_adaptive_confidence_min), out - 0.12)
    return out
