"""Stage 1 worker: clean 00_docling_raw md -> 01_docling_clean00 md. See docs/LAYER_SERVICES.md."""

from __future__ import annotations

import os
from pathlib import Path

import redis

from app.config.loader import load_config, log_worker_counts
from app.config.models import AppConfig
from app.queue.job import QueueJob, QueueStage
from app.queue.redis_queue import JobQueue
from app.workers.runner import handle_job_failure, run_with_timeout
from app.workers.stage1 import run_stage1_job

STAGE = QueueStage.docling_raw2docling_clean00
WORKER_NAME = "docling_raw2docling_clean00"


def boot() -> tuple[AppConfig, JobQueue]:
    config_path = Path(os.environ.get("CONFIG_PATH", "config.yaml"))
    env_path = Path(os.environ.get("ENV_PATH", ".env"))
    config = load_config(config_path, env_path)
    log_worker_counts(config)
    redis_url = os.environ["REDIS_URL"]
    queue = JobQueue.for_stage(
        redis.from_url(redis_url),
        STAGE,
        config.queues.docling_raw2docling_clean00,
        retry_attempts=config.runtime.redis_retry_attempts,
        retry_base_delay_seconds=config.runtime.retry_base_delay_seconds,
    )
    return config, queue


def main() -> None:
    config, queue = boot()

    while True:
        job = queue.dequeue(timeout=5)
        if job is None:
            continue
        if job.stage != STAGE:
            queue.enqueue(job)
            continue
        try:
            run_with_timeout(
                lambda current_job=job: run_stage1_job(config, current_job),
                config.runtime.process_timeout_seconds,
            )
        except Exception as exc:
            handle_job_failure(
                shared_root=config.shared_root,
                stage="docling_clean00",
                resolved_path=job.resolved_path,
                worker=WORKER_NAME,
                exc=exc,
            )


if __name__ == "__main__":
    main()
