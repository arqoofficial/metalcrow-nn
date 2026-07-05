"""Redis queue backend tests."""

import fakeredis
import pytest

from app.queue.jobs import IndexPathJob
from app.queue.redis_queue import RedisJobQueue


@pytest.fixture
def redis_queue(monkeypatch: pytest.MonkeyPatch) -> RedisJobQueue:
    fake_client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake_client)
    return RedisJobQueue("redis://localhost:6379/0", namespace="test_ns")


def test_enqueue_dequeue(redis_queue: RedisJobQueue) -> None:
    job = IndexPathJob(
        subfolder_path="reports",
        source_subfolder="01_docling_clean00",
        correlation_id="cid-redis",
    )
    job_id = redis_queue.enqueue(job)
    item = redis_queue.dequeue()
    assert item is not None
    dequeued_id, dequeued_job = item
    assert dequeued_id == job_id
    assert dequeued_job.correlation_id == "cid-redis"


def test_failed_jobs_recording(redis_queue: RedisJobQueue) -> None:
    job = IndexPathJob(
        subfolder_path="reports",
        source_subfolder="01_docling_clean00",
        correlation_id="cid-fail",
    )
    redis_queue.record_failure("jid-1", job, "failure")
    failed = redis_queue.failed_jobs()
    assert len(failed) == 1
    assert failed[0][0] == "jid-1"
    assert failed[0][1].subfolder_path == "reports"
    assert failed[0][2] == "failure"
