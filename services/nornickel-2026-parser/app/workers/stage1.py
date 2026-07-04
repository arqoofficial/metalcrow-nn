"""Stage 1 worker implementation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.config.models import AppConfig
from app.data.okf_io import parse_okf, serialize_okf
from app.data.okf_parser import ParserOkfPipelineInfo, ParserOkfStageRef, PipelineStageId
from app.paths import STAGE_FOLDERS, stage_okf_path
from app.queue.job import QueueJob, QueueStage
from app.workers.cleanup import CLEANER_VERSION, clean_docling_markdown
from app.workers.common import atomic_write_text
from app.workers.failure import clear_failure
from app.workers.stage0 import worker_lock


def run_stage1_job(config: AppConfig, job: QueueJob) -> bool:
    if job.stage != QueueStage.docling_raw2docling_clean00:
        return False

    in_relative = stage_okf_path(STAGE_FOLDERS["docling_raw"], job.resolved_path)
    out_relative = stage_okf_path(STAGE_FOLDERS["docling_clean00"], job.resolved_path)
    in_path = Path(config.shared_root) / in_relative
    out_path = Path(config.shared_root) / out_relative

    if not in_path.is_file():
        return False

    if out_path.is_file() and not job.enforce:
        return True

    with worker_lock(config, job):
        document = parse_okf(in_path.read_text(encoding="utf-8"))
        document.body = clean_docling_markdown(document.body)
        clear_failure(config.shared_root, "docling_clean00", job.resolved_path)
        document.frontmatter.stage = ParserOkfStageRef(
            id=PipelineStageId.docling_clean00,
            folder=STAGE_FOLDERS["docling_clean00"],
            sequence=1,
        )
        document.frontmatter.processed_at = datetime.now(timezone.utc)
        pipeline = document.frontmatter.pipeline or ParserOkfPipelineInfo()
        document.frontmatter.pipeline = pipeline.model_copy(
            update={
                "cleaner_version": CLEANER_VERSION,
                "worker": "docling_raw2docling_clean00",
            }
        )
        atomic_write_text(out_path, serialize_okf(document))
    return True
