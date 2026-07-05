"""Reindex scheduling tests."""

from pathlib import Path

import fakeredis

from app.paths import raw_to_stage0_okf
from app.queue.job import QueueStage
from app.queue.redis_queue import JobQueue
from app.services.reindex_service import enqueue_reindex
from tests.workers.conftest import make_config, seed_raw


def test_reindex_skips_stage0_when_output_exists(shared_root: Path) -> None:
    config = make_config(shared_root)
    rel = "UPLOAD_DATA/reports/q1__v01.pdf"
    seed_raw(shared_root, rel)
    client = fakeredis.FakeRedis(decode_responses=True)
    stage0_queue = JobQueue.for_stage(client, QueueStage.raw2docling_raw)
    stage1_queue = JobQueue.for_stage(client, QueueStage.docling_raw2docling_clean00)

    stage0_okf = shared_root / raw_to_stage0_okf(rel)
    stage0_okf.parent.mkdir(parents=True, exist_ok=True)
    stage0_okf.write_text("---\ntitle: test\n---\n# body\n", encoding="utf-8")

    stage0_count, stage1_count = enqueue_reindex(config, stage0_queue, stage1_queue)
    assert stage0_count == 0
    assert stage1_count == 1
    assert stage1_queue.depth() == 1


def test_reindex_enqueues_stage0_for_missing_output(shared_root: Path) -> None:
    config = make_config(shared_root)
    rel = "UPLOAD_DATA/reports/q1__v01.pdf"
    seed_raw(shared_root, rel)
    client = fakeredis.FakeRedis(decode_responses=True)
    stage0_queue = JobQueue.for_stage(client, QueueStage.raw2docling_raw)
    stage1_queue = JobQueue.for_stage(client, QueueStage.docling_raw2docling_clean00)

    stage0_count, stage1_count = enqueue_reindex(config, stage0_queue, stage1_queue)
    assert stage0_count == 1
    assert stage1_count == 0
    assert stage0_queue.depth() == 1
