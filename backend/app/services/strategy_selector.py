"""
Мета-выбор стратегии по режиму рынка, волатильности и (опционально) confidence ML.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.ml.regime import MarketRegime, RegimeSnapshot


@dataclass
class StrategySelection:
    strategy_id: str
    reason_ru: str
    regime: str
    meta: dict = field(default_factory=dict)


def select_strategy_for_regime(
    reg: MarketRegime,
    snap: RegimeSnapshot,
    atr_pct: float,
    *,
    ml_confidence_hint: float | None = None,
    settings: Settings | None = None,
) -> StrategySelection:
    s = settings or get_settings()
    if (s.strategy_router_mode or "regime").strip().lower() == "ml_only":
        return StrategySelection(
            strategy_id="ml_hybrid",
            reason_ru="Режим маршрутизации: только ML (STRATEGY_ROUTER_MODE=ml_only).",
            regime=reg.value,
            meta={"router": "ml_only"},
        )
    if not s.multi_strategy_enabled:
        return StrategySelection(
            strategy_id="ml_hybrid",
            reason_ru="Мульти-стратегии выключены (MULTI_STRATEGY_ENABLED=false).",
            regime=reg.value,
            meta={"router": "disabled"},
        )

    # Опционально: при очень низкой уверенности ML в прошлом тике — предпочесть mean rev во флэте
    meta: dict = {"adx": round(snap.adx, 4), "atr_pct": round(atr_pct, 6), "vol_cluster": round(snap.vol_cluster_ratio, 4)}

    if reg == MarketRegime.HIGH_VOLATILITY:
        return StrategySelection(
            strategy_id="volatility_breakout",
            reason_ru="Режим высокой волатильности / кластер — импульсный breakout.",
            regime=reg.value,
            meta=meta,
        )
    if reg in (MarketRegime.TREND_UP, MarketRegime.TREND_DOWN):
        if ml_confidence_hint is not None and ml_confidence_hint < float(s.strategy_ml_conf_floor):
            return StrategySelection(
                strategy_id="trend_following",
                reason_ru="Тренд по ADX/DI; ML confidence ниже порога — усиление rule-based тренда.",
                regime=reg.value,
                meta={**meta, "ml_confidence_hint": ml_confidence_hint},
            )
        return StrategySelection(
            strategy_id="trend_following",
            reason_ru="Трендовый режим (ADX) — следование тренду с трейлингом.",
            regime=reg.value,
            meta=meta,
        )
    if reg == MarketRegime.FLAT:
        return StrategySelection(
            strategy_id="mean_reversion",
            reason_ru="Флэт — mean reversion от средней (z-score), узкий тейк.",
            regime=reg.value,
            meta=meta,
        )
    return StrategySelection(
        strategy_id="ml_hybrid",
        reason_ru="Fallback — гибрид XGBoost + LSTM.",
        regime=reg.value,
        meta=meta,
    )


def explain_selection(sel: StrategySelection) -> dict:
    return {
        "strategy_id": sel.strategy_id,
        "reason_ru": sel.reason_ru,
        "regime": sel.regime,
        "meta": sel.meta,
    }
