"""Step 03 - QueueJob model tests."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.queue.job import QueueJob, QueueStage


def _sample_job() -> QueueJob:
    return QueueJob(
        job_id="job-123",
        requested_path="reports/q1.pdf",
        resolved_path="UPLOAD_DATA/reports/q1__v02.pdf",
        stage=QueueStage.raw2docling_raw,
        enforce=True,
        enqueued_at=datetime(2026, 7, 3, 3, 0, tzinfo=timezone.utc),
    )


def test_queue_job_roundtrip_json() -> None:
    job = _sample_job()
    restored = QueueJob.from_json(job.to_json())
    assert restored == job


def test_queue_job_requires_all_fields() -> None:
    with pytest.raises(ValidationError):
        QueueJob.model_validate(
            {
                "job_id": "job-123",
                "requested_path": "reports/q1.pdf",
            }
        )


def test_queue_job_uses_requested_resolved_paths() -> None:
    job = _sample_job()
    payload = job.model_dump()
    assert payload["requested_path"] == "reports/q1.pdf"
    assert payload["resolved_path"] == "UPLOAD_DATA/reports/q1__v02.pdf"
    assert "canonical_path" not in payload
    assert "raw_absolute_path" not in payload
