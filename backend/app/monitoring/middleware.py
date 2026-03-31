import time
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings
from app.monitoring.statsd_client import get_metrics


class MetricsMiddleware(BaseHTTPMiddleware):
    """HTTP latency + счётчики в DogStatsD (Datadog Agent)."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if path.startswith("/api/health"):
            return await call_next(request)
        metrics_path = get_settings().prometheus_metrics_path or "/metrics"
        if path == metrics_path or path.rstrip("/") == metrics_path.rstrip("/"):
            return await call_next(request)

        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            m = get_metrics()
            m.timing(
                "http.request.duration_ms",
                elapsed_ms,
                {"method": request.method, "path": _route_bucket(path), "status": str(status)},
            )
            m.increment("http.request.count", 1, {"method": request.method, "status_class": str(status // 100)})


def _route_bucket(path: str) -> str:
    if path.startswith("/api/"):
        parts = path.strip("/").split("/")
        if len(parts) >= 2:
            return f"/api/{parts[1]}/*"
    return path[:80]
