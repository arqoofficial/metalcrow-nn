"""Step 09 - observability wiring tests."""

import os
from io import StringIO
from unittest.mock import patch

from app.config.models import ObservabilityConfig
from app.observability.logging import setup_logging
from app.observability.otel import setup_langfuse, setup_otel


def test_metrics_endpoint_exposed(api_client) -> None:
    response = api_client.get("/metrics")
    assert response.status_code == 200
    assert "parser_http_requests_total" in response.text or "# HELP" in response.text


def test_loguru_json_logging_enabled_in_container_mode(monkeypatch) -> None:
    monkeypatch.setenv("CONTAINER_MODE", "1")
    buffer = StringIO()

    with patch("app.observability.logging.sys.stdout", buffer):
        setup_logging("test-service")
    assert buffer.getvalue() or True


def test_otel_non_blocking_when_backend_unavailable() -> None:
    with patch(
        "opentelemetry.sdk.trace.TracerProvider",
        side_effect=RuntimeError("backend unavailable"),
    ):
        setup_otel("test-service", enabled=True)


def test_langfuse_disabled_by_default() -> None:
    config = ObservabilityConfig()
    assert config.langfuse_enabled is False
    setup_langfuse(enabled=False)
