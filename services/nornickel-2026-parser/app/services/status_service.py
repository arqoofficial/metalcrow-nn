"""Processing status derivation."""

from __future__ import annotations

from pathlib import Path

from app.config.models import AppConfig
from app.paths import raw_to_stage0_okf, raw_to_stage1_okf
from app.presentation.schemas import FileStatusResponse, ProcessingStatus, StageStatus
from app.queue.redis_queue import JobQueue
from app.services.path_resolution import (
    queued_resolved_paths,
    resolve_exact_raw_path,
    worker_lock_exists,
)

from app.workers.failure import has_failure

STATUS_RANK = {
    ProcessingStatus.failed: 0,
    ProcessingStatus.processing: 1,
    ProcessingStatus.queued: 2,
    ProcessingStatus.pending: 3,
    ProcessingStatus.done: 4,
}

STAGE_DEFINITIONS = (
    ("docling_raw", raw_to_stage0_okf),
    ("docling_clean00", raw_to_stage1_okf),
)


def derive_stage_status(
    *,
    shared_root: str,
    resolved_path: str,
    okf_relative: str,
    stage_name: str,
    queue: JobQueue | None,
    worker_suffix: str,
) -> ProcessingStatus:
    if has_failure(shared_root, stage_name, resolved_path):
        return ProcessingStatus.failed
    okf_path = Path(shared_root) / okf_relative
    if okf_path.is_file():
        return ProcessingStatus.done
    if queue is not None and resolved_path in queued_resolved_paths(queue):
        return ProcessingStatus.queued
    if worker_lock_exists(shared_root, resolved_path, worker_suffix):
        return ProcessingStatus.processing
    return ProcessingStatus.pending


def build_file_status(config: AppConfig, path: str, stage0_queue: JobQueue, stage1_queue: JobQueue) -> FileStatusResponse:
    shared_root = config.shared_root
    resolved = resolve_exact_raw_path(path, shared_root)
    if resolved is None:
        raise LookupError("file not found")

    stages: list[StageStatus] = []
    for stage_name, mapper in STAGE_DEFINITIONS:
        okf_relative = mapper(resolved.relative)
        queue = stage0_queue if stage_name == "docling_raw" else stage1_queue
        status = derive_stage_status(
            shared_root=shared_root,
            resolved_path=resolved.relative,
            okf_relative=okf_relative,
            stage_name=stage_name,
            queue=queue,
            worker_suffix=config.locks.worker_suffix,
        )
        stages.append(
            StageStatus(stage=stage_name, status=status, okf_path=okf_relative)
        )

    overall = min(stages, key=lambda item: STATUS_RANK[item.status]).status
    return FileStatusResponse(
        requested_path=path,
        resolved_path=resolved.relative,
        is_final=True,
        overall_status=overall,
        stages=stages,
    )


def stage0_output_exists(shared_root: str, resolved_path: str) -> bool:
    return (Path(shared_root) / raw_to_stage0_okf(resolved_path)).is_file()
