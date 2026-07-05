"""Reindex scheduling."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.config.models import AppConfig
from app.paths import raw_to_stage1_okf
from app.queue.job import QueueJob, QueueStage
from app.queue.redis_queue import JobQueue
from app.services.path_resolution import list_raw_concrete_paths
from app.services.status_service import stage0_output_exists


def enqueue_reindex(
    config: AppConfig,
    stage0_queue: JobQueue,
    stage1_queue: JobQueue,
    *,
    enforce: bool = False,
) -> tuple[int, int]:
    shared_root = config.shared_root
    stage0_count = 0
    stage1_count = 0
    for resolved_path in list_raw_concrete_paths(shared_root):
        if enforce or not stage0_output_exists(shared_root, resolved_path):
            stage0_queue.enqueue(
                QueueJob(
                    requested_path=resolved_path,
                    resolved_path=resolved_path,
                    stage=QueueStage.raw2docling_raw,
                    enforce=enforce,
                    enqueued_at=datetime.now(timezone.utc),
                )
            )
            stage0_count += 1
            continue

        stage1_path = Path(shared_root) / raw_to_stage1_okf(resolved_path)
        if not stage1_path.is_file():
            stage1_queue.enqueue(
                QueueJob(
                    requested_path=resolved_path,
                    resolved_path=resolved_path,
                    stage=QueueStage.docling_raw2docling_clean00,
                    enforce=enforce,
                    enqueued_at=datetime.now(timezone.utc),
                )
            )
            stage1_count += 1
    return stage0_count, stage1_count
