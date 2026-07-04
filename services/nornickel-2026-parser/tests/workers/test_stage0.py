"""Step 06 - stage 0 worker tests."""

from pathlib import Path
from unittest.mock import patch

import fakeredis

from app.locks.files import worker_lock_path
from app.paths import raw_to_stage0_okf
from app.queue.job import QueueJob, QueueStage
from app.queue.redis_queue import JobQueue
from app.workers import stage0 as stage0_module
from app.workers.stage0 import run_stage0_job
from tests.workers.conftest import make_config, seed_raw


def _job() -> QueueJob:
    return QueueJob(
        requested_path="reports/q1.pdf",
        resolved_path="UPLOAD_DATA/reports/q1__v01.pdf",
        stage=QueueStage.raw2docling_raw,
    )


def test_stage0_consumes_from_stage0_queue_only() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    stage0 = JobQueue.for_stage(client, QueueStage.raw2docling_raw)
    stage1 = JobQueue.for_stage(client, QueueStage.docling_raw2docling_clean00)
    job = _job()
    stage0.enqueue(job)
    assert stage0.dequeue(timeout=1) == job


def test_stage0_creates_worker_lock_and_cleans_on_success(shared_root: Path) -> None:
    config = make_config(shared_root)
    seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    lock = worker_lock_path(str(shared_root), _job().resolved_path, ".worker.lock")
    run_stage0_job(config, _job())
    assert not lock.exists()


def test_stage0_writes_stage0_okf_path_with_versioned_name(shared_root: Path) -> None:
    config = make_config(shared_root)
    seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    run_stage0_job(config, _job())
    okf = shared_root / raw_to_stage0_okf("UPLOAD_DATA/reports/q1__v01.pdf")
    assert okf.is_file()
    assert okf.name == "q1__v01.pdf.md"


def test_stage0_enqueues_stage1_job(shared_root: Path) -> None:
    config = make_config(shared_root)
    seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    next_job = run_stage0_job(config, _job())
    assert next_job is not None
    assert next_job.stage == QueueStage.docling_raw2docling_clean00


def test_stage0_atomic_write_uses_temp_then_rename(shared_root: Path) -> None:
    config = make_config(shared_root)
    seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    calls: list[Path] = []
    original = stage0_module.atomic_write_text

    def spy(target: Path, content: str) -> None:
        calls.append(target)
        original(target, content)

    with patch.object(stage0_module, "atomic_write_text", side_effect=spy):
        run_stage0_job(config, _job())
    assert calls
