from collections.abc import Callable

from app.strategies.aggressive_scalp import signal_aggressive_scalp
from app.strategies.fallback_strategy import signal_fallback_technical
from app.strategies.mean_reversion import signal_mean_reversion
from app.strategies.trend_following import signal_trend_following
from app.strategies.types import StrategySignal
from app.strategies.volatility_breakout import signal_volatility_breakout

RULE_STRATEGY_REGISTRY: dict[str, Callable[..., StrategySignal | None]] = {
    "trend_following": signal_trend_following,
    "mean_reversion": signal_mean_reversion,
    "volatility_breakout": signal_volatility_breakout,
    "fallback_technical": signal_fallback_technical,
    "aggressive_scalp": signal_aggressive_scalp,
}

__all__ = [
    "StrategySignal",
    "RULE_STRATEGY_REGISTRY",
    "signal_trend_following",
    "signal_mean_reversion",
    "signal_volatility_breakout",
    "signal_fallback_technical",
    "signal_aggressive_scalp",
]
