"""Retry and backoff helpers for Redis and lock I/O."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def with_retry(
    operation: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.1,
    retry_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except retry_exceptions as exc:
            last_error = exc
            if attempt >= max_attempts:
                break
            time.sleep(base_delay_seconds * attempt)
    assert last_error is not None
    raise last_error
