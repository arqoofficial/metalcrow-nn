"""Prometheus metrics helpers."""

from __future__ import annotations

from prometheus_client import Counter, generate_latest

REQUEST_COUNT = Counter("parser_http_requests_total", "Total HTTP requests", ["method", "path"])


def metrics_payload() -> bytes:
    return generate_latest()
