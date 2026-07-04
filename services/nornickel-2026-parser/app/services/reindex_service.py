"""Reindex scheduling."""

from __future__ import annotations

from datetime import datetime, timezone

from app.config.models import AppConfig
from app.queue.job import QueueJob, QueueStage
from app.queue.redis_queue import JobQueue
from app.services.path_resolution import list_raw_concrete_paths


def enqueue_reindex(
    config: AppConfig,
    stage0_queue: JobQueue,
    *,
    enforce: bool = False,
) -> int:
    paths = list_raw_concrete_paths(config.shared_root)
    count = 0
    for resolved_path in paths:
        stage0_queue.enqueue(
            QueueJob(
                requested_path=resolved_path,
                resolved_path=resolved_path,
                stage=QueueStage.raw2docling_raw,
                enforce=enforce,
                enqueued_at=datetime.now(timezone.utc),
            )
        )
        count += 1
    return count
