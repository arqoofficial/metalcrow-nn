"""Redis-backed queue backend for index_path jobs."""

from __future__ import annotations

import json
import uuid
from typing import cast

import redis

from app.queue.jobs import IndexPathJob


class RedisJobQueue:
    def __init__(self, redis_url: str, namespace: str = "advance_rag") -> None:
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._queue_key = f"{namespace}:queue"
        self._failed_key = f"{namespace}:failed"

    def enqueue(self, job: IndexPathJob) -> str:
        job_id = str(uuid.uuid4())
        payload = {"job_id": job_id, "job": job.model_dump()}
        self._client.rpush(self._queue_key, json.dumps(payload))
        return job_id

    def dequeue(self) -> tuple[str, IndexPathJob] | None:
        raw = cast(str | None, self._client.lpop(self._queue_key))
        if raw is None:
            return None
        payload = json.loads(raw)
        job_id = payload["job_id"]
        job = IndexPathJob.model_validate(payload["job"])
        return job_id, job

    def size(self) -> int:
        return int(cast(int, self._client.llen(self._queue_key)))

    def record_failure(self, job_id: str, job: IndexPathJob, error: str) -> None:
        payload = {"job_id": job_id, "job": job.model_dump(), "error": error}
        self._client.rpush(self._failed_key, json.dumps(payload))

    def failed_jobs(self) -> list[tuple[str, IndexPathJob, str]]:
        rows = cast(list[str], self._client.lrange(self._failed_key, 0, -1))
        failed: list[tuple[str, IndexPathJob, str]] = []
        for row in rows:
            payload = json.loads(row)
            failed.append(
                (
                    payload["job_id"],
                    IndexPathJob.model_validate(payload["job"]),
                    payload["error"],
                )
            )
        return failed
