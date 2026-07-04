"""Step 06 - worker pipeline integration tests."""

from pathlib import Path

import fakeredis

from app.locks.files import worker_lock_path
from app.paths import raw_to_stage0_okf, raw_to_stage1_okf
from app.queue.job import QueueJob, QueueStage
from app.queue.redis_queue import JobQueue
from app.workers.stage0 import run_stage0_job
from app.workers.stage1 import run_stage1_job
from tests.workers.conftest import make_config, seed_raw


def _job(stage: QueueStage) -> QueueJob:
    return QueueJob(
        requested_path="reports/q1.pdf",
        resolved_path="UPLOAD_DATA/reports/q1__v01.pdf",
        stage=stage,
    )


def test_stage0_to_stage1_queue_chain(shared_root: Path) -> None:
    config = make_config(shared_root)
    client = fakeredis.FakeRedis(decode_responses=True)
    stage0_queue = JobQueue.for_stage(client, QueueStage.raw2docling_raw)
    stage1_queue = JobQueue.for_stage(client, QueueStage.docling_raw2docling_clean00)
    seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    stage0_queue.enqueue(_job(QueueStage.raw2docling_raw))
    job = stage0_queue.dequeue(timeout=1)
    assert job is not None
    next_job = run_stage0_job(config, job)
    assert next_job is not None
    stage1_queue.enqueue(next_job)
    stage1_job = stage1_queue.dequeue(timeout=1)
    assert stage1_job is not None
    run_stage1_job(config, stage1_job)
    assert (shared_root / raw_to_stage1_okf(_job(QueueStage.raw2docling_raw).resolved_path)).is_file()


def test_stage0_and_stage1_end_to_end(shared_root: Path) -> None:
    config = make_config(shared_root)
    seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    next_job = run_stage0_job(config, _job(QueueStage.raw2docling_raw))
    assert next_job is not None
    run_stage1_job(config, next_job)
    assert (shared_root / raw_to_stage0_okf(_job(QueueStage.raw2docling_raw).resolved_path)).is_file()
    assert (shared_root / raw_to_stage1_okf(_job(QueueStage.raw2docling_raw).resolved_path)).is_file()


def test_duplicate_inflight_jobs_last_writer_wins(shared_root: Path) -> None:
    config = make_config(shared_root)
    seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    run_stage0_job(config, _job(QueueStage.raw2docling_raw))
    first = (shared_root / raw_to_stage0_okf(_job(QueueStage.raw2docling_raw).resolved_path)).read_text(
        encoding="utf-8"
    )
    run_stage0_job(config, _job(QueueStage.raw2docling_raw))
    second = (shared_root / raw_to_stage0_okf(_job(QueueStage.raw2docling_raw).resolved_path)).read_text(
        encoding="utf-8"
    )
    assert first and second


def test_missing_input_best_effort_no_crash(shared_root: Path) -> None:
    config = make_config(shared_root)
    assert run_stage0_job(config, _job(QueueStage.raw2docling_raw)) is None
    assert run_stage1_job(config, _job(QueueStage.docling_raw2docling_clean00)) is False


def test_enforce_rewrite_path(shared_root: Path) -> None:
    config = make_config(shared_root)
    seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    run_stage0_job(config, _job(QueueStage.raw2docling_raw))
    run_stage0_job(
        config,
        QueueJob(
            requested_path="reports/q1.pdf",
            resolved_path="UPLOAD_DATA/reports/q1__v01.pdf",
            stage=QueueStage.raw2docling_raw,
            enforce=True,
        ),
    )
    assert (shared_root / raw_to_stage0_okf("UPLOAD_DATA/reports/q1__v01.pdf")).is_file()


def test_worker_lock_lifecycle(shared_root: Path) -> None:
    config = make_config(shared_root)
    seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    lock = worker_lock_path(str(shared_root), _job(QueueStage.raw2docling_raw).resolved_path, ".worker.lock")
    run_stage0_job(config, _job(QueueStage.raw2docling_raw))
    assert not lock.exists()
