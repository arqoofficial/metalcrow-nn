"""Dedicated queue worker entrypoint."""

from __future__ import annotations

import signal
import time
from pathlib import Path

from app.config.settings import get_settings
from app.data.chroma_adapter import create_chroma_adapter
from app.indexing.service import IndexingService
from app.queue.jobs import JobQueue, QueueBackend, Worker, make_index_path_handler
from app.queue.redis_queue import RedisJobQueue


def main() -> None:
    base = Path(__file__).resolve().parents[1]
    runtime, secrets = get_settings(base_dir=str(base))
    chroma = create_chroma_adapter(runtime, base, secrets=secrets)
    indexing = IndexingService(runtime, chroma, base)
    queue: QueueBackend
    if runtime.queue.backend == "redis":
        if not secrets.redis_url:
            raise ValueError("Queue backend 'redis' requires REDIS_URL in .env")
        queue = RedisJobQueue(secrets.redis_url)
    else:
        queue = JobQueue()

    worker = Worker(queue, make_index_path_handler(indexing), runtime.queue.poll_interval_sec)
    worker.start()

    running = True

    def _stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while running:
        time.sleep(0.5)
    worker.stop()


if __name__ == "__main__":
    main()
