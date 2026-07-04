"""Step 06 - stage 1 worker tests."""

from pathlib import Path

import fakeredis

from app.data.okf_io import serialize_okf
from app.data.okf_parser import (
    PARSER_OKF_TYPE,
    DataSource,
    ParserOkfDocument,
    ParserOkfFrontmatter,
    ParserOkfRawRef,
    ParserOkfStageRef,
    PipelineStageId,
)
from app.paths import raw_to_stage0_okf, raw_to_stage1_okf
from app.queue.job import QueueJob, QueueStage
from app.queue.redis_queue import JobQueue
from app.workers.stage1 import run_stage1_job
from tests.workers.conftest import make_config, seed_raw
from datetime import datetime, timezone


def _job() -> QueueJob:
    return QueueJob(
        requested_path="reports/q1.pdf",
        resolved_path="UPLOAD_DATA/reports/q1__v01.pdf",
        stage=QueueStage.docling_raw2docling_clean00,
    )


def _seed_stage0(shared_root: Path) -> None:
    seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    frontmatter = ParserOkfFrontmatter(
        type=PARSER_OKF_TYPE,
        title="q1",
        raw=ParserOkfRawRef(
            path="reports/q1.pdf",
            source=DataSource.upload_data,
            absolute_path="UPLOAD_DATA/reports/q1__v01.pdf",
            sha256="a" * 64,
        ),
        stage=ParserOkfStageRef(
            id=PipelineStageId.docling_raw,
            folder="00_docling_raw",
            sequence=0,
        ),
        processed_at=datetime.now(timezone.utc),
    )
    document = ParserOkfDocument(frontmatter=frontmatter, body="line one\n\nline two\n")
    okf = shared_root / raw_to_stage0_okf("UPLOAD_DATA/reports/q1__v01.pdf")
    okf.parent.mkdir(parents=True, exist_ok=True)
    okf.write_text(serialize_okf(document), encoding="utf-8")


def test_stage1_consumes_from_stage1_queue_only() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    queue = JobQueue.for_stage(client, QueueStage.docling_raw2docling_clean00)
    job = _job()
    queue.enqueue(job)
    assert queue.dequeue(timeout=1) == job


def test_stage1_reads_stage0_okf_and_writes_stage1_okf(shared_root: Path) -> None:
    config = make_config(shared_root)
    _seed_stage0(shared_root)
    run_stage1_job(config, _job())
    out = shared_root / raw_to_stage1_okf("UPLOAD_DATA/reports/q1__v01.pdf")
    assert out.is_file()


def test_stage1_updates_frontmatter_stage_fields(shared_root: Path) -> None:
    from app.data.okf_io import parse_okf

    config = make_config(shared_root)
    _seed_stage0(shared_root)
    run_stage1_job(config, _job())
    out = shared_root / raw_to_stage1_okf("UPLOAD_DATA/reports/q1__v01.pdf")
    parsed = parse_okf(out.read_text(encoding="utf-8"))
    assert parsed.frontmatter.stage.id.value == "docling_clean00"
