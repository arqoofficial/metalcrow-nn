"""Shared worker helpers."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.config.models import AppConfig
from app.locks.files import create_worker_lock, remove_lock, worker_lock_path
from app.queue.job import QueueJob


def atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(target)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def with_worker_lock(config: AppConfig, job: QueueJob):
    lock_path = worker_lock_path(
        config.shared_root,
        job.resolved_path,
        config.locks.worker_suffix,
    )
    create_worker_lock(lock_path)
    try:
        yield lock_path
    finally:
        remove_lock(lock_path)
