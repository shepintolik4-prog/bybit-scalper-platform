"""
Институциональное портфельное управление: risk parity, mean-variance,
ограничения по корреляциям, динамическая аллокация.

Предполагается, что входные доходности — по одному ряду на актив (одинаковая частота),
индекс времени выровнен. Ковариации оцениваются на окне lookback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from app.config import get_settings


class AllocationMethod(str, Enum):
    EQUAL_WEIGHT = "equal_weight"
    INVERSE_VOLATILITY = "inverse_volatility"
    RISK_PARITY_ERC = "risk_parity_erc"
    MIN_VARIANCE = "min_variance"
    MEAN_VARIANCE = "mean_variance"


@dataclass(frozen=True)
class PortfolioConstraints:
    """Box constraints и мягкий потолок по парной корреляции (после оптимизации)."""

    w_min: float = 0.0
    w_max: float = 0.45
    sum_weights: float = 1.0
    max_pair_correlation: float = 0.85
    correlation_shrinkage: float = 0.15  # к диагонали (стабильность ковариации)


@dataclass
class AllocationResult:
    symbols: list[str]
    weights: np.ndarray
    capital_usdt: np.ndarray
    method: str
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _validate_returns(returns: pd.DataFrame, min_obs: int = 30) -> pd.DataFrame:
    r = returns.dropna()
    if r.shape[0] < min_obs:
        raise ValueError(f"Недостаточно наблюдений: {r.shape[0]} < {min_obs}")
    return r


def annualize_covariance(returns: pd.DataFrame, periods_per_year: int = 365 * 24 * 12) -> np.ndarray:
    """
    Годовая ковариация из доходностей.
    Для 5m: periods_per_year ≈ 105120 (условно); для дневных — 365.
    """
    r = returns.dropna()
    cov = r.cov().values
    return np.asarray(cov, dtype=float) * float(periods_per_year)


def shrink_covariance(cov: np.ndarray, shrink: float) -> np.ndarray:
    """Простое сжатие к диагонали (Ledoit–Wolf упрощённо)."""
    shrink = float(np.clip(shrink, 0.0, 1.0))
    d = np.diag(np.diag(cov))
    return (1.0 - shrink) * cov + shrink * d


def equal_weights(n: int) -> np.ndarray:
    return np.ones(n, dtype=float) / float(n)


def inverse_volatility_weights(cov: np.ndarray) -> np.ndarray:
    vol = np.sqrt(np.clip(np.diag(cov), 1e-18, None))
    inv = 1.0 / vol
    w = inv / inv.sum()
    return w


def _risk_contributions(w: np.ndarray, cov: np.ndarray) -> np.ndarray:
    port_var = float(w @ cov @ w)
    if port_var <= 0:
        return np.ones_like(w) / len(w)
    mrc = cov @ w
    rc = w * mrc / np.sqrt(port_var)
    return rc


def risk_parity_erc_weights(cov: np.ndarray, max_iter: int = 80, tol: float = 1e-8) -> np.ndarray:
    """
    Equal Risk Contribution (приближение Spinu/Maillard–Roncalli через минимизацию дисперсии вкладов).
    """
    n = cov.shape[0]
    x0 = inverse_volatility_weights(cov)

    def objective(w: np.ndarray) -> float:
        rc = _risk_contributions(w, cov)
        target = np.ones(n) / n
        return float(np.sum((rc / rc.sum() - target) ** 2))

    cons = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}
    bounds = tuple((1e-8, 1.0) for _ in range(n))
    res = minimize(objective, x0, method="SLSQP", bounds=bounds, constraints=cons, options={"maxiter": max_iter})
    w = np.maximum(res.x, 1e-8)
    return w / w.sum()


def min_variance_weights(cov: np.ndarray, w_min: float, w_max: float) -> np.ndarray:
    n = cov.shape[0]

    def obj(w: np.ndarray) -> float:
        return float(w @ cov @ w)

    x0 = equal_weights(n)
    cons = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}
    bounds = tuple((w_min, w_max) for _ in range(n))
    res = minimize(obj, x0, method="SLSQP", bounds=bounds, constraints=cons)
    w = np.maximum(res.x, 0.0)
    return w / w.sum()


def mean_variance_weights(
    mu: np.ndarray,
    cov: np.ndarray,
    risk_aversion: float,
    w_min: float,
    w_max: float,
) -> np.ndarray:
    """
    max w^T mu - (lambda/2) w^T Sigma w, sum w = 1, box bounds.
    """
    n = cov.shape[0]
    gamma = max(float(risk_aversion), 1e-8)

    def neg_utility(w: np.ndarray) -> float:
        return float(-(w @ mu) + 0.5 * gamma * float(w @ cov @ w))

    x0 = equal_weights(n)
    cons = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}
    bounds = tuple((w_min, w_max) for _ in range(n))
    res = minimize(neg_utility, x0, method="SLSQP", bounds=bounds, constraints=cons)
    w = np.maximum(res.x, 0.0)
    return w / w.sum()


def clip_pair_correlation_exposure(
    weights: np.ndarray,
    corr: np.ndarray,
    max_corr: float,
    dampen: float = 0.35,
) -> np.ndarray:
    """
    Эвристика: если для пары (i,j) corr > max_corr и оба веса заметны — уменьшаем меньший вес.
    Не гарантирует глобальный оптимум, зато устойчиво и дёшево по CPU.
    """
    w = weights.copy().astype(float)
    n = len(w)
    for i in range(n):
        for j in range(i + 1, n):
            if corr[i, j] <= max_corr:
                continue
            if w[i] <= 0 or w[j] <= 0:
                continue
            if w[i] < w[j]:
                w[i] *= 1.0 - dampen
            else:
                w[j] *= 1.0 - dampen
    s = w.sum()
    if s <= 0:
        return equal_weights(n)
    return w / s


def dynamic_blend_with_signals(
    base_weights: np.ndarray,
    signal_scores: np.ndarray,
    temperature: float = 1.0,
    floor: float = 0.05,
) -> np.ndarray:
    """
    Динамическая аллокация: базовые веса * softmax(signal * T), затем нормализация и floor.
    signal_scores — произвольная шкала (например combined_edge или z-score).
    """
    z = np.asarray(signal_scores, dtype=float) * float(temperature)
    z = z - np.max(z)
    ex = np.exp(np.clip(z, -20, 20))
    tilt = ex / ex.sum()
    w = base_weights * tilt
    w = np.maximum(w, floor / len(w))
    return w / w.sum()


class PortfolioManager:
    """
    Фасад для multi-asset распределения капитала.

    Не ходит в биржу; только математика и ограничения. Интеграция с `bot_engine` — на уровне
    «сколько USDT выделить на символ i в этом цикле».
    """

    def __init__(self, constraints: PortfolioConstraints | None = None) -> None:
        self.constraints = constraints or PortfolioConstraints()
        self.settings = get_settings()

    def optimize_weights(
        self,
        returns: pd.DataFrame,
        method: AllocationMethod = AllocationMethod.RISK_PARITY_ERC,
        *,
        expected_returns: pd.Series | None = None,
        risk_aversion: float = 8.0,
        periods_per_year: int = 365 * 24 * 12,
    ) -> AllocationResult:
        returns = _validate_returns(returns)
        symbols = list(returns.columns)
        cov_ann = annualize_covariance(returns, periods_per_year=periods_per_year)
        cov_ann = shrink_covariance(cov_ann, self.constraints.correlation_shrinkage)
        corr = returns.corr().values

        if method == AllocationMethod.EQUAL_WEIGHT:
            w = equal_weights(len(symbols))
            diag = {"note": "equal_weight"}
        elif method == AllocationMethod.INVERSE_VOLATILITY:
            w = inverse_volatility_weights(cov_ann)
            diag = {"note": "inverse_vol"}
        elif method == AllocationMethod.RISK_PARITY_ERC:
            w = risk_parity_erc_weights(cov_ann)
            diag = {"note": "erc"}
        elif method == AllocationMethod.MIN_VARIANCE:
            w = min_variance_weights(cov_ann, self.constraints.w_min, self.constraints.w_max)
            diag = {"note": "min_var"}
        elif method == AllocationMethod.MEAN_VARIANCE:
            if expected_returns is None:
                mu = returns.mean().values * float(periods_per_year)
            else:
                mu = expected_returns.reindex(symbols).values.astype(float)
            w = mean_variance_weights(mu, cov_ann, risk_aversion, self.constraints.w_min, self.constraints.w_max)
            diag = {"note": "mean_variance", "risk_aversion": risk_aversion}
        else:
            raise ValueError(f"Unknown method: {method}")

        w = np.clip(w, self.constraints.w_min, self.constraints.w_max)
        w = w / w.sum()
        w = clip_pair_correlation_exposure(w, corr, self.constraints.max_pair_correlation)

        return AllocationResult(
            symbols=symbols,
            weights=w,
            capital_usdt=np.zeros_like(w),
            method=method.value,
            diagnostics=diag,
        )

    def allocate_capital(
        self,
        equity_usdt: float,
        returns: pd.DataFrame,
        method: AllocationMethod = AllocationMethod.RISK_PARITY_ERC,
        *,
        expected_returns: pd.Series | None = None,
        signal_scores: pd.Series | None = None,
        risk_aversion: float = 8.0,
        portfolio_fraction: float = 1.0,
        periods_per_year: int = 365 * 24 * 12,
    ) -> AllocationResult:
        """
        Полный цикл: веса → доли капитала в USDT.
        `portfolio_fraction` — доля эквити, участвующая в стратегии (остальное кэш/резерв).
        Если задан `signal_scores` (индекс = символы), применяется динамический tilt поверх базовых весов.
        """
        base = self.optimize_weights(
            returns,
            method=method,
            expected_returns=expected_returns,
            risk_aversion=risk_aversion,
            periods_per_year=periods_per_year,
        )
        w = base.weights.copy()
        if signal_scores is not None:
            aligned = signal_scores.reindex(base.symbols).fillna(0.0).values
            w = dynamic_blend_with_signals(
                w,
                aligned,
                temperature=float(self.settings.portfolio_signal_temperature),
            )
        cap_total = max(0.0, float(equity_usdt)) * float(np.clip(portfolio_fraction, 0.0, 1.0))
        capital = w * cap_total
        return AllocationResult(
            symbols=base.symbols,
            weights=w,
            capital_usdt=capital,
            method=base.method,
            diagnostics={
                **base.diagnostics,
                "equity_usdt": equity_usdt,
                "portfolio_fraction": portfolio_fraction,
                "dynamic_signals": signal_scores is not None,
            },
        )


def allocation_to_dict(result: AllocationResult) -> dict[str, Any]:
    return {
        "symbols": result.symbols,
        "weights": {s: float(w) for s, w in zip(result.symbols, result.weights, strict=True)},
        "capital_usdt": {s: float(c) for s, c in zip(result.symbols, result.capital_usdt, strict=True)},
        "method": result.method,
        "diagnostics": result.diagnostics,
    }
