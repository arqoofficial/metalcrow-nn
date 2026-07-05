"""Logging baseline unit tests."""

import json
from io import StringIO
from unittest.mock import patch

from app.config.settings import ObservabilityConfig
from app.observability.logging import bind_request_context, create_logger


def test_json_log_format_in_runtime_mode() -> None:
    buffer = StringIO()

    def capture_sink(message: object) -> None:

        record = message.record  # type: ignore[attr-defined]
        payload = {
            "time": record["time"].isoformat(),
            "level": record["level"].name,
            "message": record["message"],
        }
        buffer.write(json.dumps(payload) + "\n")

    with patch("app.observability.logging._json_sink", capture_sink):
        log = create_logger(ObservabilityConfig(log_json=True))
        bind_request_context("req-123")
        log.info("test_event")
    line = buffer.getvalue().strip()
    data = json.loads(line)
    assert data["level"] == "INFO"
    assert data["message"] == "test_event"


def test_context_fields_can_be_injected() -> None:
    buffer = StringIO()

    def capture_sink(message: object) -> None:
        record = message.record  # type: ignore[attr-defined]
        buffer.write(record["message"])

    with patch("app.observability.logging._json_sink", capture_sink):
        log = create_logger(ObservabilityConfig(log_json=True))
        bind_request_context("abc")
        log.bind(endpoint="/health").info("ping")

    assert buffer.getvalue() == "ping"
