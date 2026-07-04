"""Step 03 - Redis stage queue helper tests."""

from datetime import datetime, timezone

import fakeredis

from app.queue.job import QueueJob, QueueStage
from app.queue.redis_queue import JobQueue, queue_name_for_stage


def _sample_job(stage: QueueStage) -> QueueJob:
    return QueueJob(
        job_id=f"job-{stage.value}",
        requested_path="reports/q1.pdf",
        resolved_path="UPLOAD_DATA/reports/q1__v01.pdf",
        stage=stage,
        enforce=False,
        enqueued_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )


def test_push_pop_stage0_queue() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    queue = JobQueue.for_stage(client, QueueStage.raw2docling_raw)
    job = _sample_job(QueueStage.raw2docling_raw)

    queue.enqueue(job)
    popped = queue.dequeue(timeout=1)

    assert popped == job


def test_push_pop_stage1_queue() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    queue = JobQueue.for_stage(client, QueueStage.docling_raw2docling_clean00)
    job = _sample_job(QueueStage.docling_raw2docling_clean00)

    queue.enqueue(job)
    popped = queue.dequeue(timeout=1)

    assert popped == job


def test_stage_queues_are_isolated() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    stage0 = JobQueue.for_stage(client, QueueStage.raw2docling_raw)
    stage1 = JobQueue.for_stage(client, QueueStage.docling_raw2docling_clean00)
    job0 = _sample_job(QueueStage.raw2docling_raw)
    job1 = _sample_job(QueueStage.docling_raw2docling_clean00)

    stage0.enqueue(job0)
    stage1.enqueue(job1)

    assert stage0.dequeue(timeout=1) == job0
    assert stage1.dequeue(timeout=1) == job1
    assert queue_name_for_stage(QueueStage.raw2docling_raw) != queue_name_for_stage(
        QueueStage.docling_raw2docling_clean00
    )
