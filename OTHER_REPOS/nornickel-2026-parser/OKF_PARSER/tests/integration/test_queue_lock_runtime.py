"""Step 03 - queue and lock runtime integration tests."""

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import fakeredis

from app.locks.files import create_worker_lock, remove_lock, worker_lock_path
from app.queue.job import QueueJob, QueueStage
from app.queue.redis_queue import JobQueue

REPO_ROOT = Path(__file__).resolve().parents[2]
CLEAN_LOCK_SCRIPT = REPO_ROOT / "clean_lock.sh"


def _sample_job(stage: QueueStage) -> QueueJob:
    return QueueJob(
        job_id=f"integration-{stage.value}",
        requested_path="reports/q1.pdf",
        resolved_path="UPLOAD_DATA/reports/q1__v01.pdf",
        stage=stage,
        enforce=False,
        enqueued_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )


def test_enqueue_dequeue_both_stages() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    stage0 = JobQueue.for_stage(client, QueueStage.raw2docling_raw)
    stage1 = JobQueue.for_stage(client, QueueStage.docling_raw2docling_clean00)
    job0 = _sample_job(QueueStage.raw2docling_raw)
    job1 = _sample_job(QueueStage.docling_raw2docling_clean00)

    stage0.enqueue(job0)
    stage1.enqueue(job1)

    assert stage0.dequeue(timeout=1) == job0
    assert stage1.dequeue(timeout=1) == job1


def test_worker_lock_visible_during_hold(tmp_path: Path) -> None:
    resolved = "UPLOAD_DATA/reports/q1__v01.pdf"
    lock_path = worker_lock_path(str(tmp_path), resolved, ".worker.lock")

    create_worker_lock(lock_path)
    assert lock_path.exists()

    remove_lock(lock_path)
    assert not lock_path.exists()


def test_clean_lock_clears_stale_runtime_locks(tmp_path: Path) -> None:
    upload_lock = tmp_path / "RAW_DATA" / "stale.upload.lock"
    worker_lock = tmp_path / "UPLOAD_DATA" / "stale.worker.lock"
    upload_lock.parent.mkdir(parents=True)
    worker_lock.parent.mkdir(parents=True, exist_ok=True)
    upload_lock.write_text("stale", encoding="utf-8")
    worker_lock.write_text("stale", encoding="utf-8")

    env = os.environ.copy()
    env["SHARED_ROOT"] = str(tmp_path)
    result = subprocess.run(
        [str(CLEAN_LOCK_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert not upload_lock.exists()
    assert not worker_lock.exists()
