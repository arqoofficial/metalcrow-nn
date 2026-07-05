"""Pipeline failure marker tests."""

from app.services.status_service import build_file_status, derive_stage_status
from app.workers.failure import has_failure, record_failure
from tests.workers.conftest import make_config, seed_raw


def test_record_failure_marks_stage_failed(shared_root) -> None:
    resolved = "UPLOAD_DATA/reports/q1.pdf"
    seed_raw(shared_root, resolved)
    record_failure(
        str(shared_root),
        stage="docling_raw",
        resolved_path=resolved,
        worker="raw2docling_raw",
        error="conversion failed",
    )
    assert has_failure(str(shared_root), "docling_raw", resolved)


def test_build_file_status_reports_failed_stage(shared_root, config_files) -> None:
    import fakeredis
    from unittest.mock import patch

    from app.queue.redis_queue import JobQueue
    from app.queue.job import QueueStage

    config_path, _ = config_files
    config = make_config(shared_root)
    resolved = "UPLOAD_DATA/reports/q1.pdf"
    seed_raw(shared_root, resolved)
    record_failure(
        str(shared_root),
        stage="docling_raw",
        resolved_path=resolved,
        worker="raw2docling_raw",
        error="boom",
    )

    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    stage0 = JobQueue.for_stage(fake_redis, QueueStage.raw2docling_raw)
    stage1 = JobQueue.for_stage(fake_redis, QueueStage.docling_raw2docling_clean00)

    status = build_file_status(config, "reports/q1.pdf", stage0, stage1)
    assert status.stages[0].status.value == "failed"
