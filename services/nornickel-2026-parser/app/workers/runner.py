"""Shared worker execution helpers."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Callable, TypeVar

T = TypeVar("T")


def run_with_timeout(operation: Callable[[], T], timeout_seconds: int) -> T:
    if timeout_seconds <= 0:
        return operation()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(operation)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as exc:
            raise TimeoutError(f"operation timed out after {timeout_seconds}s") from exc


def handle_job_failure(
    *,
    shared_root: str,
    stage: str,
    resolved_path: str,
    worker: str,
    exc: Exception,
) -> None:
    from app.workers.failure import record_failure

    record_failure(
        shared_root,
        stage=stage,
        resolved_path=resolved_path,
        worker=worker,
        error=str(exc),
    )
    print(f"job {resolved_path} failed in {worker}: {exc}", file=sys.stderr)
