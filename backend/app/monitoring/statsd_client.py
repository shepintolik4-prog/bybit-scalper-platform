"""
DogStatsD-совместимая отправка метрик (UDP). Работает с Datadog Agent / любым StatsD.
Без внешних зависимостей — fire-and-forget, не блокирует запрос при сбое сети.
"""
from __future__ import annotations

import logging
import socket
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


class DogStatsDClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._enabled = bool(self._settings.metrics_enabled and self._settings.dd_agent_host)
        self._host = (self._settings.dd_agent_host or "127.0.0.1").strip()
        self._port = int(self._settings.dd_dogstatsd_port or 8125)
        self._prefix = (self._settings.metrics_prefix or "bybit_scalper").strip(".")
        self._tags = self._parse_tags(self._settings.metrics_default_tags)

    @staticmethod
    def _parse_tags(raw: str) -> list[str]:
        if not raw.strip():
            return []
        return [t.strip() for t in raw.split(",") if t.strip()]

    def _full_tags(self, extra: dict[str, str] | None) -> str:
        parts = list(self._tags)
        if extra:
            for k, v in extra.items():
                parts.append(f"{k}:{v}")
        if not parts:
            return ""
        return "|#" + ",".join(parts)

    def _send(self, line: str) -> None:
        if not self._enabled:
            return
        try:
            data = (line + "\n").encode("utf-8")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.2)
                s.sendto(data, (self._host, self._port))
        except OSError as e:
            logger.debug("metrics send failed: %s", e)

    def timing(self, metric: str, ms: float, tags: dict[str, str] | None = None) -> None:
        name = f"{self._prefix}.{metric}" if self._prefix else metric
        tag_str = self._full_tags(tags)
        self._send(f"{name}:{ms:.4f}|ms{tag_str}")

    def increment(self, metric: str, value: int = 1, tags: dict[str, str] | None = None) -> None:
        name = f"{self._prefix}.{metric}" if self._prefix else metric
        tag_str = self._full_tags(tags)
        self._send(f"{name}:{value}|c{tag_str}")

    def gauge(self, metric: str, value: float, tags: dict[str, str] | None = None) -> None:
        name = f"{self._prefix}.{metric}" if self._prefix else metric
        tag_str = self._full_tags(tags)
        self._send(f"{name}:{value}|g{tag_str}")


_client: DogStatsDClient | None = None


def get_metrics() -> DogStatsDClient:
    global _client
    if _client is None:
        _client = DogStatsDClient()
    return _client


def record_timing(metric: str, ms: float, **tags: Any) -> None:
    t = {k: str(v) for k, v in tags.items()}
    get_metrics().timing(metric, ms, t or None)


def incr(metric: str, value: int = 1, **tags: Any) -> None:
    t = {k: str(v) for k, v in tags.items()}
    get_metrics().increment(metric, value, t or None)
