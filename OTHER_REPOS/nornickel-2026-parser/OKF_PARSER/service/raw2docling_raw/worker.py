"""Stage 0 worker: raw file (pdf, ...) -> OKF md in 00_docling_raw/. See docs/LAYER_SERVICES.md."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import redis

from app.config.loader import load_config, log_worker_counts
from app.config.models import AppConfig
from app.queue.job import QueueJob, QueueStage
from app.queue.redis_queue import JobQueue
from app.workers.runner import handle_job_failure, run_with_timeout
from app.workers.stage0 import run_stage0_job

STAGE = QueueStage.raw2docling_raw
WORKER_NAME = "raw2docling_raw"


def boot() -> tuple[AppConfig, JobQueue, JobQueue]:
    project_root = Path(__file__).resolve().parents[2]
    config_path = Path(os.environ.get("CONFIG_PATH", project_root / "config.yaml"))
    env_path = Path(os.environ.get("ENV_PATH", project_root / ".env"))
    config = load_config(config_path, env_path)
    log_worker_counts(config)
    redis_url = os.environ["REDIS_URL"]
    client = redis.from_url(redis_url)
    stage0_queue = JobQueue.for_stage(
        client,
        STAGE,
        config.queues.raw2docling_raw,
        retry_attempts=config.runtime.redis_retry_attempts,
        retry_base_delay_seconds=config.runtime.retry_base_delay_seconds,
    )
    stage1_queue = JobQueue.for_stage(
        client,
        QueueStage.docling_raw2docling_clean00,
        config.queues.docling_raw2docling_clean00,
        retry_attempts=config.runtime.redis_retry_attempts,
        retry_base_delay_seconds=config.runtime.retry_base_delay_seconds,
    )
    return config, stage0_queue, stage1_queue


def _process_job(config: AppConfig, stage0_queue: JobQueue, stage1_queue: JobQueue, job: QueueJob) -> None:
    next_job = run_with_timeout(
        lambda: run_stage0_job(config, job),
        config.runtime.process_timeout_seconds,
    )
    if next_job is not None:
        stage1_queue.enqueue(next_job)


def main() -> None:
    config, stage0_queue, stage1_queue = boot()

    while True:
        job = stage0_queue.dequeue(timeout=5)
        if job is None:
            continue
        if job.stage != STAGE:
            stage0_queue.enqueue(job)
            continue
        try:
            _process_job(config, stage0_queue, stage1_queue, job)
        except Exception as exc:
            handle_job_failure(
                shared_root=config.shared_root,
                stage="docling_raw",
                resolved_path=job.resolved_path,
                worker=WORKER_NAME,
                exc=exc,
            )


if __name__ == "__main__":
    main()
