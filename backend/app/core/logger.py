from __future__ import annotations

import logging

from app.logging_config import get_logger, setup_logging


def init_logging(level: str = "INFO") -> None:
    setup_logging(level)


def log() -> logging.Logger:
    return get_logger("scalper")


__all__ = ["init_logging", "get_logger", "log"]

