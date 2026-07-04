"""Lock file utilities for upload allocation and worker runtime."""

from __future__ import annotations

import os
from pathlib import Path

from app.queue.retry import with_retry


def upload_lock_path(shared_root: str, resolved_path: str, upload_suffix: str) -> Path:
    return Path(shared_root) / f"{resolved_path.strip('/')}{upload_suffix}"


def worker_lock_path(shared_root: str, resolved_path: str, worker_suffix: str) -> Path:
    return Path(shared_root) / f"{resolved_path.strip('/')}{worker_suffix}"


def create_upload_lock(
    lock_path: Path,
    *,
    retry_attempts: int = 3,
    retry_base_delay_seconds: float = 0.1,
) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    def operation() -> None:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)

    try:
        operation()
    except FileExistsError:
        raise
    except OSError:
        with_retry(
            operation,
            max_attempts=retry_attempts,
            base_delay_seconds=retry_base_delay_seconds,
            retry_exceptions=(OSError,),
        )


def create_worker_lock(
    lock_path: Path,
    *,
    retry_attempts: int = 3,
    retry_base_delay_seconds: float = 0.1,
) -> None:
    create_upload_lock(
        lock_path,
        retry_attempts=retry_attempts,
        retry_base_delay_seconds=retry_base_delay_seconds,
    )


def remove_lock(
    lock_path: Path,
    *,
    retry_attempts: int = 3,
    retry_base_delay_seconds: float = 0.1,
) -> None:
    def operation() -> None:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            return

    with_retry(
        operation,
        max_attempts=retry_attempts,
        base_delay_seconds=retry_base_delay_seconds,
    )
