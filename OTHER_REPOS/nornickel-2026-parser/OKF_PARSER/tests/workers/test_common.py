"""Step 06 - shared worker behavior tests."""

from pathlib import Path

from app.paths import raw_to_stage0_okf
from app.queue.job import QueueJob, QueueStage
from app.workers.stage0 import run_stage0_job
from tests.raw_data_samples import discover_raw_data_pdfs
from tests.workers.conftest import make_config, seed_raw


def _job(enforce: bool = False) -> QueueJob:
    return QueueJob(
        requested_path="reports/q1.pdf",
        resolved_path="UPLOAD_DATA/reports/q1__v01.pdf",
        stage=QueueStage.raw2docling_raw,
        enforce=enforce,
    )


def test_missing_input_is_best_effort_no_crash(shared_root: Path) -> None:
    config = make_config(shared_root)
    assert run_stage0_job(config, _job()) is None


def test_duplicate_jobs_last_writer_wins(shared_root: Path) -> None:
    config = make_config(shared_root)
    pdfs = discover_raw_data_pdfs(2)
    seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf", source_pdf=pdfs[0])
    run_stage0_job(config, _job())
    first = (shared_root / raw_to_stage0_okf(_job().resolved_path)).read_text(encoding="utf-8")
    seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf", source_pdf=pdfs[1])
    run_stage0_job(config, _job())
    second = (shared_root / raw_to_stage0_okf(_job().resolved_path)).read_text(encoding="utf-8")
    assert first != second


def test_enforce_true_overwrites_existing_output(shared_root: Path) -> None:
    config = make_config(shared_root)
    seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    run_stage0_job(config, _job())
    before = (shared_root / raw_to_stage0_okf(_job().resolved_path)).read_text(encoding="utf-8")
    run_stage0_job(config, _job(enforce=True))
    after = (shared_root / raw_to_stage0_okf(_job().resolved_path)).read_text(encoding="utf-8")
    assert before
    assert after
