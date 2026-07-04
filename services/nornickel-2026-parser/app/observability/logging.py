"""Loguru structured logging setup."""

from __future__ import annotations

import os
import sys

from loguru import logger


def setup_logging(service_name: str) -> None:
    logger.remove()
    container_mode = os.environ.get("CONTAINER_MODE", "").lower() in {"1", "true", "yes"}
    if container_mode:
        logger.add(
            sys.stdout,
            serialize=True,
            backtrace=False,
            diagnose=False,
            enqueue=True,
        )
    else:
        logger.add(sys.stderr, enqueue=True)
    logger.configure(extra={"service": service_name})
    logger.info("logging initialized for {}", service_name)
