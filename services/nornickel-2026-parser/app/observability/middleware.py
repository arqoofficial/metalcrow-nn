"""HTTP metrics middleware."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.observability.metrics import REQUEST_COUNT


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        config = getattr(request.app.state, "config", None)
        if config is None or not config.observability.metrics_enabled:
            return response
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        REQUEST_COUNT.labels(method=request.method, path=path).inc()
        return response
