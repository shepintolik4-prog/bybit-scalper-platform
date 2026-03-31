"""
Расширенная инициализация Sentry: окружение, SQLAlchemy, базовый контекст.
"""
from __future__ import annotations

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from app.config import get_settings


def init_sentry() -> None:
    s = get_settings()
    if not s.sentry_dsn_backend:
        return
    sentry_sdk.init(
        dsn=s.sentry_dsn_backend,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
        ],
        environment=s.sentry_environment,
        traces_sample_rate=float(s.sentry_traces_sample_rate),
        profiles_sample_rate=float(s.sentry_profiles_sample_rate),
        send_default_pii=False,
    )
    sentry_sdk.set_tag("service", "bybit-scalper-api")


def capture_trade_event(message: str, *, level: str = "info", extra: dict | None = None) -> None:
    """Бизнес-событие (сделка, отказ, стоп) — breadcrumb + optional message."""
    if not get_settings().sentry_dsn_backend:
        return
    with sentry_sdk.push_scope() as scope:
        scope.set_level(level)
        if extra:
            for k, v in extra.items():
                scope.set_extra(k, v)
        sentry_sdk.capture_message(message, level=level)
