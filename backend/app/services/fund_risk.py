"""
Ограничения риска в духе quant/multi-manager: концентрация, кластеры ликвидности,
опциональный vol-targeting по эквити, выбор кандидата с учётом ERC/tilt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.orm import EquityPoint
from app.services.correlation_service import fetch_returns_matrix
from app.services.portfolio_manager import AllocationMethod, PortfolioConstraints, PortfolioManager


@dataclass(frozen=True)
class FundRiskCheck:
    ok: bool
    reason: str
    metrics: dict[str, Any]


def _major_set() -> set[str]:
    s = get_settings()
    return {x.strip() for x in s.fund_major_symbols.split(",") if x.strip()}


def is_major_symbol(symbol: str) -> bool:
    return symbol in _major_set()


def _gross_by_symbol(
    open_positions: list[tuple[str, float, int]],
    candidate: tuple[str, float, int] | None,
) -> dict[str, float]:
    """Номинал USDT по символу (маржа × плечо)."""
    out: dict[str, float] = {}
    for sym, margin, lev in open_positions:
        out[sym] = out.get(sym, 0.0) + float(margin) * int(lev)
    if candidate:
        sym, margin, lev = candidate
        out[sym] = out.get(sym, 0.0) + float(margin) * int(lev)
    return out


def check_fund_limits(
    candidate_symbol: str,
    margin_usdt: float,
    leverage: int,
    equity: float,
    open_positions: list[tuple[str, float, int]],
) -> FundRiskCheck:
    """
    Проверка перед открытием: доля крупнейшего имени в gross book, доля альт-кластера.
    """
    s = get_settings()
    if not s.fund_risk_enabled:
        return FundRiskCheck(True, "disabled", {})

    cand = (candidate_symbol, margin_usdt, leverage)
    gross_by = _gross_by_symbol(open_positions, cand)
    gross_total = sum(gross_by.values())
    metrics: dict[str, Any] = {
        "gross_total_usdt": round(gross_total, 4),
        "n_book": len(gross_by),
    }
    if gross_total <= 0:
        return FundRiskCheck(True, "empty_book", metrics)

    mx = max(gross_by.values())
    single_share = mx / gross_total
    metrics["max_single_name_share"] = round(float(single_share), 4)
    # Первая позиция в книге — всегда 100% в одном имени; лимит концентрации имеет смысл при ≥2 имён в буке.
    if len(open_positions) > 0 and single_share > s.fund_max_single_name_gross_pct + 1e-9:
        return FundRiskCheck(
            False,
            f"single_name_concentration share={single_share:.3f}>{s.fund_max_single_name_gross_pct}",
            metrics,
        )

    if s.fund_max_alt_cluster_share < 1.0:
        majors = _major_set()
        alt_gross = sum(g for sym, g in gross_by.items() if sym not in majors)
        alt_share = alt_gross / gross_total
        metrics["alt_cluster_share"] = round(float(alt_share), 4)
        if alt_share > s.fund_max_alt_cluster_share + 1e-9:
            return FundRiskCheck(
                False,
                f"alt_cluster_cap share={alt_share:.3f}>{s.fund_max_alt_cluster_share}",
                metrics,
            )

    return FundRiskCheck(True, "ok", metrics)


def vol_scale_from_equity_history(db: Session, *, min_points: int = 12) -> float:
    """
    Если realized vol (относит. изменения эквити) выше порога — уменьшаем размер новой позиции.
    Возвращает множитель ∈ [fund_vol_size_scale_floor, 1.0].
    """
    s = get_settings()
    if not s.fund_vol_targeting_enabled:
        return 1.0
    rows = db.query(EquityPoint.equity).order_by(desc(EquityPoint.ts)).limit(120).all()
    if len(rows) < min_points:
        return 1.0
    eq = np.array([float(r[0]) for r in reversed(rows)], dtype=float)
    if np.any(eq <= 0):
        return 1.0
    ret = np.diff(eq) / np.clip(eq[:-1], 1e-12, None)
    if len(ret) < 5:
        return 1.0
    sig = float(np.std(ret))
    if sig <= 1e-12:
        return 1.0
    if sig <= s.fund_vol_rel_threshold:
        return 1.0
    ratio = s.fund_vol_rel_threshold / sig
    return float(np.clip(ratio, s.fund_vol_size_scale_floor, 1.0))


def _parse_allocation_method(name: str) -> AllocationMethod:
    key = name.strip().lower()
    for m in AllocationMethod:
        if m.value == key:
            return m
    return AllocationMethod.RISK_PARITY_ERC


def _selection_strength(c: tuple[float, str, dict[str, Any], dict[str, float], float, Any, Any]) -> float:
    expl = c[2]
    v = expl.get("selection_score")
    if v is not None:
        return float(v)
    return abs(float(c[0]))


def pick_candidate_with_portfolio_tilt(
    candidates: list[tuple[float, str, dict[str, Any], dict[str, float], float, Any, Any]],
    equity: float,
    _universe: list[str],
) -> tuple[float, str, dict[str, Any], dict[str, float], float, Any, Any] | None:
    """
    Вместо argmax |edge|: доходности по кандидатам, ERC (+ signal tilt), скоринг |edge| × вес.
    """
    s = get_settings()
    if len(candidates) < 2:
        return max(candidates, key=_selection_strength) if candidates else None

    scores = pd.Series({c[1]: _selection_strength(c) for c in candidates})
    sym_list = list(dict.fromkeys(scores.index.tolist()))
    if len(sym_list) < 2:
        return max(candidates, key=_selection_strength)
    df = fetch_returns_matrix(sym_list, limit=160)
    if df is None or df.shape[1] < 2:
        return max(candidates, key=_selection_strength)
    cols = [c for c in sym_list if c in df.columns]
    if len(cols) < 2:
        return max(candidates, key=_selection_strength)
    df = df[cols].dropna()
    if df.shape[0] < 40 or df.shape[1] < 2:
        return max(candidates, key=_selection_strength)

    pm = PortfolioManager(
        constraints=PortfolioConstraints(
            w_max=s.fund_tilt_w_max,
            max_pair_correlation=s.fund_tilt_max_pair_correlation,
        )
    )
    method = _parse_allocation_method(s.fund_portfolio_tilt_method)
    scores = scores.reindex(df.columns).fillna(0.0)
    try:
        alloc = pm.allocate_capital(
            equity,
            df,
            method=method,
            signal_scores=scores,
            portfolio_fraction=1.0,
            periods_per_year=s.portfolio_periods_per_year,
        )
    except Exception:
        return max(candidates, key=_selection_strength)

    w_by = dict(zip(alloc.symbols, alloc.weights, strict=True))
    best = None
    best_score = -1.0
    for combined, sym, expl, feats, last_mid, reg, snap in candidates:
        w = float(w_by.get(sym, 0.0))
        ss = _selection_strength((combined, sym, expl, feats, last_mid, reg, snap))
        tilt = w * ss
        if tilt > best_score:
            best_score = tilt
            best = (combined, sym, expl, feats, last_mid, reg, snap)
    return best


def rank_candidates_for_multi_execution(
    candidates: list[tuple[float, str, dict[str, Any], dict[str, float], float, Any, Any]],
    equity: float,
    _universe: list[str],
) -> list[tuple[float, str, dict[str, Any], dict[str, float], float, Any, Any]]:
    """
    Упорядочить кандидатов для мульти-открытия за тик: дедуп по символу (лучший score),
    затем по selection_score или по ERC-tilt (если fund_portfolio_tilt_enabled).
    """
    if not candidates:
        return []
    s = get_settings()

    def strength(c: tuple[float, str, dict[str, Any], dict[str, float], float, Any, Any]) -> float:
        return _selection_strength(c)

    best_by_sym: dict[str, tuple[float, str, dict[str, Any], dict[str, float], float, Any, Any]] = {}
    for c in sorted(candidates, key=strength, reverse=True):
        sym = c[1]
        if sym not in best_by_sym:
            best_by_sym[sym] = c
    uniq = list(best_by_sym.values())

    if not s.fund_portfolio_tilt_enabled or len(uniq) < 2:
        return sorted(uniq, key=strength, reverse=True)

    scores = pd.Series({c[1]: strength(c) for c in uniq})
    sym_list = list(dict.fromkeys(scores.index.tolist()))
    if len(sym_list) < 2:
        return sorted(uniq, key=strength, reverse=True)
    df = fetch_returns_matrix(sym_list, limit=160)
    if df is None or df.shape[1] < 2:
        return sorted(uniq, key=strength, reverse=True)
    cols = [c for c in sym_list if c in df.columns]
    if len(cols) < 2:
        return sorted(uniq, key=strength, reverse=True)
    df = df[cols].dropna()
    if df.shape[0] < 40 or df.shape[1] < 2:
        return sorted(uniq, key=strength, reverse=True)

    pm = PortfolioManager(
        constraints=PortfolioConstraints(
            w_max=s.fund_tilt_w_max,
            max_pair_correlation=s.fund_tilt_max_pair_correlation,
        )
    )
    method = _parse_allocation_method(s.fund_portfolio_tilt_method)
    scores = scores.reindex(df.columns).fillna(0.0)
    try:
        alloc = pm.allocate_capital(
            equity,
            df,
            method=method,
            signal_scores=scores,
            portfolio_fraction=1.0,
            periods_per_year=s.portfolio_periods_per_year,
        )
    except Exception:
        return sorted(uniq, key=strength, reverse=True)

    w_by = dict(zip(alloc.symbols, alloc.weights, strict=True))

    def tilt_key(c: tuple[float, str, dict[str, Any], dict[str, float], float, Any, Any]) -> float:
        return float(w_by.get(c[1], 0.0)) * strength(c)

    return sorted(uniq, key=tilt_key, reverse=True)


def compute_fund_snapshot(
    equity: float,
    open_positions: list[tuple[str, float, int]],
) -> dict[str, Any]:
    """Для API / дашборда: текущие метрики книги."""
    s = get_settings()
    gross_by = _gross_by_symbol(open_positions, None)
    gross_total = sum(gross_by.values())
    majors = _major_set()
    alt_gross = sum(g for sym, g in gross_by.items() if sym not in majors)
    max_sym = max(gross_by, key=gross_by.get) if gross_by else None
    max_single = (gross_by[max_sym] / gross_total) if gross_total > 0 and max_sym else 0.0
    return {
        "fund_risk_enabled": s.fund_risk_enabled,
        "equity_usdt": round(equity, 4),
        "gross_notional_usdt": round(gross_total, 4),
        "n_positions": len(gross_by),
        "max_single_name_share": round(float(max_single), 4),
        "alt_cluster_share": round(float(alt_gross / gross_total), 4) if gross_total > 0 else 0.0,
        "gross_by_symbol": {k: round(v, 4) for k, v in sorted(gross_by.items())},
        "limits": {
            "max_single_name_gross_pct": s.fund_max_single_name_gross_pct,
            "max_alt_cluster_share": s.fund_max_alt_cluster_share,
            "max_total_exposure_ratio": s.max_total_exposure_ratio,
        },
    }
