"""Loguru logging setup and context helpers."""

from __future__ import annotations

import json
import sys
from contextvars import ContextVar
from typing import Any

from loguru import logger

from app.config.settings import ObservabilityConfig

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("job_id", default=None)


def _json_sink(message: Any) -> None:
    record = message.record
    payload = {
        "time": record["time"].isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
    }
    request_id = _request_id.get()
    job_id = _job_id.get()
    if request_id:
        payload["request_id"] = request_id
    if job_id:
        payload["job_id"] = job_id
    extra = record["extra"]
    if extra:
        payload.update({k: v for k, v in extra.items() if k not in payload})
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def create_logger(config: ObservabilityConfig | None = None) -> Any:
    logger.remove()
    cfg = config or ObservabilityConfig()
    if cfg.log_json:
        logger.add(_json_sink, level="INFO")
    else:
        logger.add(sys.stdout, level="INFO")
    return logger


def bind_request_context(request_id: str) -> None:
    _request_id.set(request_id)


def bind_job_context(job_id: str) -> None:
    _job_id.set(job_id)


def clear_context() -> None:
    _request_id.set(None)
    _job_id.set(None)


def log_config_summary(runtime_summary: dict[str, Any]) -> None:
    logger.info("startup_config", **runtime_summary)
