from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    detail: str | None = None


class RiskManager:
    """
    Initial risk manager (v1): sizing + simple global guards.
    Full integration into engine happens in engine-migration todo.
    """

    def __init__(self) -> None:
        self._s = get_settings()

    def can_open_new_trade(
        self,
        *,
        equity_usdt: float,
        daily_ret_pct: float | None = None,
        drawdown_pct: float | None = None,
        max_drawdown_pct: float | None = None,
        daily_loss_limit_pct: float | None = None,
    ) -> RiskDecision:
        s = self._s
        if equity_usdt < float(s.min_equity_usdt):
            return RiskDecision(False, "min_equity", f"equity<{float(s.min_equity_usdt)}")

        dd_limit = float(max_drawdown_pct) if max_drawdown_pct is not None else float(getattr(s, "max_drawdown_pct", 0.0))
        if dd_limit > 0 and drawdown_pct is not None and float(drawdown_pct) > dd_limit:
            return RiskDecision(False, "max_drawdown", f"dd={round(float(drawdown_pct),2)}% > {dd_limit}%")

        # Daily loss limit: 0 disables
        dlim = float(daily_loss_limit_pct) if daily_loss_limit_pct is not None else float(getattr(s, "daily_loss_limit_pct", 0.0))
        if dlim > 0 and daily_ret_pct is not None:
            if float(daily_ret_pct) <= -dlim:
                return RiskDecision(False, "daily_loss_limit", f"daily_ret={round(float(daily_ret_pct),2)}% <= -{dlim}%")

        return RiskDecision(True, "ok", None)

    def position_size_usdt(self, *, equity_usdt: float, stop_loss_pct: float) -> float:
        """
        Target notional such that loss at SL ~= risk_per_trade_pct of equity.
        """
        s = self._s
        rpct = max(0.0, min(2.0, float(s.risk_per_trade_pct)))  # cap 2% by design
        sl = max(1e-6, float(stop_loss_pct))
        risk_budget = equity_usdt * (rpct / 100.0)
        size = risk_budget / sl
        # Don't exceed margin cap (simple approximation)
        max_notional = equity_usdt * float(s.max_total_exposure_ratio)
        return float(max(0.0, min(size, max_notional)))

    def position_size_usdt_from_prices(self, *, equity_usdt: float, entry_price: float, stop_price: float) -> float:
        """
        Convenience sizing using entry/stop prices.
        """
        entry = max(1e-12, float(entry_price))
        sl = abs(float(stop_price) - entry) / entry
        return self.position_size_usdt(equity_usdt=equity_usdt, stop_loss_pct=sl)

    # --- Compatibility wrappers to keep behavior unchanged during refactor ---
    def default_stops(self, side: str, price: float, atr: float, *, regime=None, tp_scale: float = 1.0):
        from app.services.risk import default_stops as _default_stops

        return _default_stops(side, price, atr, regime=regime, tp_scale=tp_scale)

    def compute_position_size(
        self,
        *,
        equity: float,
        entry: float,
        stop_price: float,
        risk_pct: float,
        leverage: int,
        risk_mult: float = 1.0,
        regime_size_mult: float = 1.0,
    ) -> float:
        from app.services.risk import compute_position_size as _compute_position_size

        return _compute_position_size(
            equity,
            entry,
            stop_price,
            risk_pct,
            leverage,
            risk_mult=risk_mult,
            regime_size_mult=regime_size_mult,
        )

