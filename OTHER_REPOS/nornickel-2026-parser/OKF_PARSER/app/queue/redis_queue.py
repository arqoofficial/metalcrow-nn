"""Redis list queues for pipeline stages."""

from __future__ import annotations

import redis

from app.queue.job import QueueJob, QueueStage
from app.queue.retry import with_retry

MAIN_LEADER_KEY = "parser:main:leader"

DEFAULT_STAGE_QUEUE_NAMES = {
    QueueStage.raw2docling_raw: "parser:jobs:raw2docling_raw",
    QueueStage.docling_raw2docling_clean00: "parser:jobs:docling_raw2docling_clean00",
}


def queue_name_for_stage(stage: QueueStage) -> str:
    return DEFAULT_STAGE_QUEUE_NAMES[stage]


class JobQueue:
    def __init__(
        self,
        client: redis.Redis,
        queue_key: str,
        *,
        retry_attempts: int = 3,
        retry_base_delay_seconds: float = 0.1,
    ) -> None:
        self._client = client
        self._queue_key = queue_key
        self._retry_attempts = retry_attempts
        self._retry_base_delay_seconds = retry_base_delay_seconds

    @classmethod
    def for_stage(
        cls,
        client: redis.Redis,
        stage: QueueStage,
        queue_key: str | None = None,
        *,
        retry_attempts: int = 3,
        retry_base_delay_seconds: float = 0.1,
    ) -> JobQueue:
        return cls(
            client,
            queue_key or queue_name_for_stage(stage),
            retry_attempts=retry_attempts,
            retry_base_delay_seconds=retry_base_delay_seconds,
        )

    @property
    def queue_key(self) -> str:
        return self._queue_key

    def depth(self) -> int:
        return int(self._client.llen(self._queue_key))

    def enqueue(self, job: QueueJob) -> None:
        def operation() -> None:
            self._client.rpush(self._queue_key, job.to_json())

        with_retry(
            operation,
            max_attempts=self._retry_attempts,
            base_delay_seconds=self._retry_base_delay_seconds,
        )

    def dequeue(self, timeout: int = 5) -> QueueJob | None:
        def operation() -> QueueJob | None:
            item = self._client.blpop(self._queue_key, timeout=timeout)
            if item is None:
                return None
            _, payload = item
            raw = payload.decode() if isinstance(payload, bytes) else payload
            return QueueJob.from_json(raw)

        return with_retry(
            operation,
            max_attempts=self._retry_attempts,
            base_delay_seconds=self._retry_base_delay_seconds,
        )
