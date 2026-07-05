"""Queue job payloads and worker runtime."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Callable, Protocol

from loguru import logger
from opentelemetry import trace
from pydantic import BaseModel

from app.data.okf import OkfParseError
from app.data.paths import PathValidationError
from app.indexing.service import IndexingService
from app.observability.logging import bind_job_context, clear_context
from app.observability.metrics import WORKER_JOB_DURATION, WORKER_JOBS_TOTAL, metrics_enabled, span


class IndexPathJob(BaseModel):
    subfolder_path: str
    source_subfolder: str
    correlation_id: str


class QueueBackend(Protocol):
    def enqueue(self, job: IndexPathJob) -> str: ...

    def dequeue(self) -> tuple[str, IndexPathJob] | None: ...

    def size(self) -> int: ...

    def record_failure(self, job_id: str, job: IndexPathJob, error: str) -> None: ...

    def failed_jobs(self) -> list[tuple[str, IndexPathJob, str]]: ...


class JobQueue:
    def __init__(self) -> None:
        self._items: deque[tuple[str, IndexPathJob]] = deque()
        self._failed_items: deque[tuple[str, IndexPathJob, str]] = deque()
        self._lock = threading.Lock()

    def enqueue(self, job: IndexPathJob) -> str:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._items.append((job_id, job))
        return job_id

    def dequeue(self) -> tuple[str, IndexPathJob] | None:
        with self._lock:
            if not self._items:
                return None
            return self._items.popleft()

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def record_failure(self, job_id: str, job: IndexPathJob, error: str) -> None:
        with self._lock:
            self._failed_items.append((job_id, job, error))

    def failed_jobs(self) -> list[tuple[str, IndexPathJob, str]]:
        with self._lock:
            return list(self._failed_items)


class Worker:
    def __init__(
        self,
        queue: QueueBackend,
        handler: Callable[[str, IndexPathJob], None],
        poll_interval_sec: float = 1.0,
    ) -> None:
        self._queue = queue
        self._handler = handler
        self._poll_interval_sec = poll_interval_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.dequeue()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("queue_worker_dequeue_failed error={}", str(exc))
                time.sleep(self._poll_interval_sec)
                continue
            if item is None:
                time.sleep(self._poll_interval_sec)
                continue
            job_id, job = item
            bind_job_context(job_id)
            start = time.perf_counter()
            try:
                with span("worker_job", job_id=job_id):
                    try:
                        self._handler(job_id, job)
                        if metrics_enabled():
                            WORKER_JOBS_TOTAL.labels(status="success").inc()
                            WORKER_JOB_DURATION.labels(status="success").observe(
                                time.perf_counter() - start
                            )
                    except Exception as exc:
                        trace.get_current_span().record_exception(exc)
                        raise
            except Exception as exc:
                if metrics_enabled():
                    WORKER_JOBS_TOTAL.labels(status="failure").inc()
                    WORKER_JOB_DURATION.labels(status="failure").observe(
                        time.perf_counter() - start
                    )
                self._queue.record_failure(job_id, job, str(exc))
                logger.exception(
                    "queue_worker_job_failed job_id={} source_subfolder={} subfolder_path={}",
                    job_id,
                    job.source_subfolder,
                    job.subfolder_path,
                )
            finally:
                clear_context()


def make_index_path_handler(indexing: IndexingService) -> Callable[[str, IndexPathJob], None]:
    def handler(job_id: str, job: IndexPathJob) -> None:
        files = indexing.list_okf_files(job.subfolder_path, job.source_subfolder)
        indexed = 0
        errors: list[str] = []
        for file_path in files:
            relative = str(file_path.relative_to(indexing._shared_root / job.source_subfolder))
            result = indexing.index_document(relative, job.source_subfolder)
            if isinstance(result, OkfParseError | PathValidationError):
                errors.append(f"{relative}: {result.message}")
                continue
            indexed += 1
        if files and indexed == 0:
            sample = "; ".join(errors[:3])
            target = f"{job.source_subfolder}/{job.subfolder_path}"
            raise RuntimeError(f"No documents indexed under {target}: {sample}")

    return handler
