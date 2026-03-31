import math
from dataclasses import dataclass

from app.config import get_settings
from app.ml.regime import MarketRegime, regime_multipliers


@dataclass
class RiskDecision:
    size_usdt: float
    stop_distance_pct: float
    take_distance_pct: float
    trail_trigger_pct: float
    trail_offset_pct: float
    stop_price: float
    take_profit_price: float


def compute_position_size(
    equity: float,
    entry: float,
    stop_price: float,
    risk_pct: float,
    leverage: int,
    *,
    risk_mult: float = 1.0,
    regime_size_mult: float = 1.0,
) -> float:
    """Маржа в USDT с мультипликаторами динамического риска и режима."""
    s = get_settings()
    risk_pct_eff = max(0.05, risk_pct * risk_mult * regime_size_mult)
    risk_usdt = max(equity * (risk_pct_eff / 100.0), 1.0)
    dist = abs(entry - stop_price) / entry
    if dist <= 0:
        return float(min(max(5.0, equity * 0.005), equity * s.max_margin_pct_equity))
    notional = risk_usdt / dist
    max_notional = equity * max(leverage, 1) * 0.95
    notional = min(notional, max_notional)
    margin = notional / max(leverage, 1)
    margin = min(margin, equity * s.max_margin_pct_equity)
    return float(max(5.0, min(margin, equity * 0.5)))


def default_stops(
    side: str,
    price: float,
    atr: float,
    regime: MarketRegime | None = None,
    *,
    tp_scale: float = 1.0,
) -> RiskDecision:
    s = get_settings()
    if s.full_aggressive_max_flow:
        sl_dist = max(0.003, min(0.05, float(s.full_aggressive_sl_pct)))
        tp_dist = max(sl_dist * 1.02, float(s.full_aggressive_tp_pct)) * float(tp_scale)
        if s.full_aggressive_trail_enabled:
            trail_trig = float(s.full_aggressive_trail_trigger_pct)
            trail_off = float(s.full_aggressive_trail_offset_pct)
        else:
            trail_trig = sl_dist * 1.15
            trail_off = sl_dist * 0.85
        if side == "buy":
            stop = price * (1 - sl_dist)
            tp = price * (1 + tp_dist)
        else:
            stop = price * (1 + sl_dist)
            tp = price * (1 - tp_dist)
        return RiskDecision(
            size_usdt=0.0,
            stop_distance_pct=sl_dist,
            take_distance_pct=tp_dist,
            trail_trigger_pct=trail_trig,
            trail_offset_pct=trail_off,
            stop_price=float(stop),
            take_profit_price=float(tp),
        )
    if s.aggressive_scalping_mode:
        sl_dist = max(0.0025, min(0.02, float(s.scalping_sl_pct)))
        tp_dist = max(sl_dist * 1.04, float(s.scalping_tp_pct)) * float(tp_scale)
        if s.scalping_trail_enabled:
            trail_trig = float(s.scalping_trail_trigger_pct)
            trail_off = float(s.scalping_trail_offset_pct)
        else:
            trail_trig = sl_dist * 1.2
            trail_off = sl_dist * 0.82
        if side == "buy":
            stop = price * (1 - sl_dist)
            tp = price * (1 + tp_dist)
        else:
            stop = price * (1 + sl_dist)
            tp = price * (1 - tp_dist)
        return RiskDecision(
            size_usdt=0.0,
            stop_distance_pct=sl_dist,
            take_distance_pct=tp_dist,
            trail_trigger_pct=trail_trig,
            trail_offset_pct=trail_off,
            stop_price=float(stop),
            take_profit_price=float(tp),
        )
    sl_dist = max(0.003, min(0.02, (atr / price) * 1.5))
    if regime is not None:
        rm = regime_multipliers(regime)
        sl_dist *= float(rm["sl"])
    tp_dist = max(sl_dist * s.min_risk_reward, sl_dist * 1.8) * float(tp_scale)
    trail_trig = sl_dist * 1.2
    trail_off = sl_dist * 0.8
    if side == "buy":
        stop = price * (1 - sl_dist)
        tp = price * (1 + tp_dist)
    else:
        stop = price * (1 + sl_dist)
        tp = price * (1 - tp_dist)
    return RiskDecision(
        size_usdt=0.0,
        stop_distance_pct=sl_dist,
        take_distance_pct=tp_dist,
        trail_trigger_pct=trail_trig,
        trail_offset_pct=trail_off,
        stop_price=float(stop),
        take_profit_price=float(tp),
    )


def update_trail(
    side: str,
    entry: float,
    highest: float | None,
    lowest: float | None,
    current: float,
    trail_price: float | None,
    trig_pct: float,
    off_pct: float,
) -> tuple[float | None, float | None, float | None]:
    """Возвращает (highest, lowest, new_trail_price)."""
    if side == "buy":
        hi = max(highest or current, current)
        lo = lowest
        peak = hi
        if peak >= entry * (1 + trig_pct):
            new_trail = peak * (1 - off_pct)
            if trail_price is None:
                return hi, lo, new_trail
            return hi, lo, max(trail_price, new_trail)
        return hi, lo, trail_price
    else:
        lo = min(lowest or current, current)
        hi = highest
        trough = lo
        if trough <= entry * (1 - trig_pct):
            new_trail = trough * (1 + off_pct)
            if trail_price is None:
                return hi, lo, new_trail
            return hi, lo, min(trail_price, new_trail)
        return hi, lo, trail_price


def macd_confirms_side(side: str, macd_hist: float) -> bool:
    s = get_settings()
    if not s.use_macd_momentum_filter:
        return True
    if side == "buy":
        return macd_hist >= 0
    return macd_hist <= 0


def atr_invalid_dead(atr_pct: float) -> bool:
    """Только «мёртвый» ATR (NaN/inf/≤0) — для скальпинга вместо жёсткого коридора."""
    x = float(atr_pct)
    return not math.isfinite(x) or x <= 0


def macd_filter_allows_entry(
    side: str,
    macd_hist: float,
    macd_hist_prev: float | None,
    macd_hist_prev2: float | None,
) -> bool:
    s = get_settings()
    if not s.use_macd_momentum_filter:
        return True
    if s.aggressive_scalping_mode and s.scalping_relax_macd:
        h = float(macd_hist)
        a = macd_hist_prev
        b = macd_hist_prev2
        if side == "buy":
            if h >= 0:
                return True
            if a is not None and h > float(a):
                return True
            if a is not None and b is not None and h > float(a) >= float(b) * 0.88:
                return True
            return h >= -1e-5
        if h <= 0:
            return True
        if a is not None and h < float(a):
            return True
        if a is not None and b is not None and h < float(a) <= float(b) * 0.88:
            return True
        return h <= 1e-5
    return macd_confirms_side(side, macd_hist)


def passes_volatility_regime(atr_pct: float) -> bool:
    s = get_settings()
    lo = float(s.atr_pct_min)
    hi = float(s.atr_pct_max)
    if s.aggressive_mode:
        lo *= 0.55
        hi *= 1.38
    return lo <= atr_pct <= hi
