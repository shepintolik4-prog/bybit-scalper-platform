"""
Динамический риск: сжатие при просадке, лёгкое усиление при росте относительно базы.
"""
from __future__ import annotations

from app.config import get_settings


def equity_growth_factor(equity: float, baseline_equity: float) -> float:
    """>1 если эквити выше стартовой базы (мягко ограничено)."""
    s = get_settings()
    if baseline_equity <= 0:
        return 1.0
    r = equity / baseline_equity
    if r <= 1.0:
        return 1.0
    boost = 1.0 + min(s.dynamic_risk_equity_boost_max, (r - 1.0) * s.dynamic_risk_equity_boost_k)
    return float(min(boost, 1.0 + s.dynamic_risk_equity_boost_max))


def drawdown_risk_multiplier(current_dd_pct: float) -> float:
    """current_dd_pct — глубина просадки от пика, %."""
    s = get_settings()
    if current_dd_pct <= 0:
        return 1.0
    m = 1.0 - min(s.dynamic_risk_dd_compress, (current_dd_pct / max(s.max_drawdown_pct, 1e-6)) * s.dynamic_risk_dd_compress)
    return float(max(s.dynamic_risk_floor_mult, m))


def combined_risk_multiplier(equity: float, peak_equity: float, baseline_equity: float) -> float:
    s = get_settings()
    dd_pct = ((peak_equity - equity) / peak_equity * 100.0) if peak_equity > 0 else 0.0
    m_dd = drawdown_risk_multiplier(dd_pct)
    m_eq = equity_growth_factor(equity, baseline_equity)
    return float(max(s.dynamic_risk_floor_mult, min(1.2, m_dd * m_eq)))
