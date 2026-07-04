"""Stage 0 worker implementation."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.config.models import AppConfig
from app.data.okf_io import serialize_okf
from app.data.okf_parser import (
    PARSER_OKF_TYPE,
    DataSource,
    ParserOkfDocument,
    ParserOkfFrontmatter,
    ParserOkfPipelineInfo,
    ParserOkfRawRef,
    ParserOkfStageRef,
    PipelineStageId,
)
from app.locks.files import create_worker_lock, remove_lock, worker_lock_path
from app.paths import STAGE_FOLDERS, stage_okf_path
from app.queue.job import QueueJob, QueueStage
from app.workers.common import atomic_write_text, sha256_file
from app.workers.docling import convert_raw_to_markdown, docling_version
from app.workers.failure import clear_failure
from app.workers.metadata import git_info, media_type_for_path


@contextmanager
def worker_lock(config: AppConfig, job: QueueJob):
    lock_path = worker_lock_path(
        config.shared_root, job.resolved_path, config.locks.worker_suffix
    )
    create_worker_lock(lock_path)
    try:
        yield lock_path
    finally:
        remove_lock(lock_path)


def run_stage0_job(config: AppConfig, job: QueueJob) -> QueueJob | None:
    if job.stage != QueueStage.raw2docling_raw:
        return None

    raw_path = Path(config.shared_root) / job.resolved_path
    if not raw_path.is_file():
        return None

    out_relative = stage_okf_path(STAGE_FOLDERS["docling_raw"], job.resolved_path)
    out_path = Path(config.shared_root) / out_relative
    if not raw_path.is_file():
        return None

    with worker_lock(config, job):
        body = convert_raw_to_markdown(raw_path, docling_config=config.pipeline.docling)
        clear_failure(config.shared_root, "docling_raw", job.resolved_path)
        source = DataSource.upload_data
        if job.resolved_path.startswith("RAW_DATA/"):
            source = DataSource.raw_data
        frontmatter = ParserOkfFrontmatter(
            type=PARSER_OKF_TYPE,
            title=raw_path.name,
            description=f"Parsed from {job.resolved_path}",
            resource=f"okf://{job.resolved_path}",
            raw=ParserOkfRawRef(
                path=job.requested_path,
                source=source,
                absolute_path=job.resolved_path,
                sha256=sha256_file(raw_path),
                media_type=media_type_for_path(raw_path),
                size_bytes=raw_path.stat().st_size,
            ),
            stage=ParserOkfStageRef(
                id=PipelineStageId.docling_raw,
                folder=STAGE_FOLDERS["docling_raw"],
                sequence=0,
            ),
            processed_at=datetime.now(timezone.utc),
            pipeline=ParserOkfPipelineInfo(
                docling_version=docling_version(), worker="raw2docling_raw"
            ),
            git=git_info(),
        )
        document = ParserOkfDocument(frontmatter=frontmatter, body=body)
        atomic_write_text(out_path, serialize_okf(document))

    return QueueJob(
        requested_path=job.requested_path,
        resolved_path=job.resolved_path,
        stage=QueueStage.docling_raw2docling_clean00,
        enforce=job.enforce,
    )
