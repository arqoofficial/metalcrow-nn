"""Queue unit and integration tests."""

import time

from app.indexing.service import IndexingService
from app.observability.metrics import WORKER_JOBS_TOTAL
from app.queue.jobs import IndexPathJob, JobQueue, Worker, make_index_path_handler


def test_enqueue_dequeue_semantics() -> None:
    queue = JobQueue()
    job = IndexPathJob(
        subfolder_path="reports",
        source_subfolder="01_docling_clean00",
        correlation_id="cid-1",
    )
    job_id = queue.enqueue(job)
    assert queue.size() == 1
    item = queue.dequeue()
    assert item is not None
    assert item[0] == job_id
    assert queue.dequeue() is None


def test_worker_processes_jobs(test_app) -> None:
    app, tmp_path, shared_tree, _ = test_app
    state = app.state.app_state
    indexing = IndexingService(state.runtime, state.chroma, tmp_path)
    queue = JobQueue()
    processed: list[str] = []

    def handler(job_id: str, job: IndexPathJob) -> None:
        processed.append(job_id)
        make_index_path_handler(indexing)(job_id, job)

    worker = Worker(queue, handler, poll_interval_sec=0.1)
    worker.start()
    queue.enqueue(
        IndexPathJob(
            subfolder_path="reports",
            source_subfolder="01_docling_clean00",
            correlation_id="cid-2",
        )
    )
    time.sleep(1.5)
    worker.stop()
    assert processed


def test_worker_failure_records_dead_letter_and_metrics() -> None:
    queue = JobQueue()

    def failing_handler(job_id: str, job: IndexPathJob) -> None:
        raise RuntimeError("boom")

    failure_counter = WORKER_JOBS_TOTAL.labels(status="failure")
    before = failure_counter._value.get()

    worker = Worker(queue, failing_handler, poll_interval_sec=0.05)
    worker.start()
    queue.enqueue(
        IndexPathJob(
            subfolder_path="reports",
            source_subfolder="01_docling_clean00",
            correlation_id="cid-failure",
        )
    )
    time.sleep(0.3)
    worker.stop()

    after = failure_counter._value.get()
    assert after == before + 1
    failed = queue.failed_jobs()
    assert len(failed) == 1
    assert failed[0][0]
    assert "boom" in failed[0][2]


def test_worker_recovers_from_dequeue_error() -> None:
    class FlakyQueue:
        def __init__(self) -> None:
            self._queue = JobQueue()
            self._raised = False

        def enqueue(self, job: IndexPathJob) -> str:
            return self._queue.enqueue(job)

        def dequeue(self) -> tuple[str, IndexPathJob] | None:
            if not self._raised:
                self._raised = True
                raise RuntimeError("temporary redis failure")
            return self._queue.dequeue()

        def size(self) -> int:
            return self._queue.size()

        def record_failure(self, job_id: str, job: IndexPathJob, error: str) -> None:
            self._queue.record_failure(job_id, job, error)

        def failed_jobs(self) -> list[tuple[str, IndexPathJob, str]]:
            return self._queue.failed_jobs()

    queue = FlakyQueue()
    processed: list[str] = []

    def handler(job_id: str, job: IndexPathJob) -> None:
        processed.append(job_id)

    worker = Worker(queue, handler, poll_interval_sec=0.05)
    worker.start()
    queue.enqueue(
        IndexPathJob(
            subfolder_path="reports",
            source_subfolder="01_docling_clean00",
            correlation_id="cid-flaky",
        )
    )
    time.sleep(0.3)
    worker.stop()

    assert processed
