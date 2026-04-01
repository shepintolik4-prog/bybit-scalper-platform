from __future__ import annotations

"""
Compatibility wrapper: for now delegates to legacy BotEngine.

Purpose: provide a stable import path (`app.engine.trading_engine`) so API and
new modules depend on the engine layer, not `services/*`.
Next refactor steps will move internals from `services/bot_engine.py` here.
"""

from app.services.bot_engine import engine as legacy_engine


class TradingEngine:
    def ensure_worker(self) -> None:
        legacy_engine.ensure_worker()

    def stop(self) -> None:
        legacy_engine.stop()

    def __getattr__(self, name: str):
        # Compatibility: allow API/routes to access legacy fields like _last_tick_seconds.
        return getattr(legacy_engine, name)


engine = TradingEngine()

