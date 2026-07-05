"""Refresh panel state from API and filesystem."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import redis

from app.config.models import AppConfig
from app.queue.job import QueueStage
from app.queue.redis_queue import JobQueue
from admin_panel.api_client import ApiClient
from admin_panel.state import ErrorSource, PanelState, QueueDepthRow, ServiceRow, Severity


def refresh_state(config: AppConfig, state: PanelState) -> None:
    started = time.perf_counter()
    now = datetime.now(timezone.utc)
    client = ApiClient(config)

    api_up = client.health_check()
    ready = client.ready_check() if api_up else False
    stage0_depth, stage1_depth = _queue_depths(config)
    state.queue_depths = [
        QueueDepthRow(
            component="service/raw2docling_raw",
            queue_key=config.queues.raw2docling_raw,
            depth=stage0_depth,
        ),
        QueueDepthRow(
            component="service/docling_raw2docling_clean00",
            queue_key=config.queues.docling_raw2docling_clean00,
            depth=stage1_depth,
        ),
    ]

    state.services = [
        ServiceRow(
            component="service/main",
            status="UP" if ready else ("DEGRADED" if api_up else "DOWN"),
            details="ready" if ready else ("health ok" if api_up else "API unreachable"),
            updated_at=now,
        ),
        _worker_row(
            component="service/raw2docling_raw",
            api_up=api_up,
            configured=config.workers.raw2docling_raw,
            queue_depth=stage0_depth,
            now=now,
        ),
        _worker_row(
            component="service/docling_raw2docling_clean00",
            api_up=api_up,
            configured=config.workers.docling_raw2docling_clean00,
            queue_depth=stage1_depth,
            now=now,
        ),
        _redis_row(now),
        _shared_row(config, now),
    ]

    try:
        state.statistics = client.get_statistics()
    except Exception as exc:
        state.statistics = None
        state.add_error(
            f"statistics fetch failed: {exc}",
            severity=Severity.ERROR,
            source=ErrorSource.api,
            max_size=config.admin_panel.error_buffer_size,
        )

    if config.admin_panel.show_lock_files:
        lock_count = _count_lock_files(config.shared_root)
        if lock_count:
            state.add_error(
                f"detected {lock_count} lock files under SHARED",
                severity=Severity.INFO,
                source=ErrorSource.services,
                max_size=config.admin_panel.error_buffer_size,
            )

    state.last_refresh_at = now
    state.last_refresh_ms = (time.perf_counter() - started) * 1000


def _worker_row(
    *,
    component: str,
    api_up: bool,
    configured: int,
    queue_depth: int | None,
    now: datetime,
) -> ServiceRow:
    if not api_up:
        return ServiceRow(
            component=component,
            status="DOWN",
            details="API unreachable",
            updated_at=now,
        )
    depth = queue_depth if queue_depth is not None else "n/a"
    status = "UP" if queue_depth is not None else "DEGRADED"
    return ServiceRow(
        component=component,
        status=status,
        details=f"workers={configured} queue={depth}",
        updated_at=now,
    )


def _queue_depths(config: AppConfig) -> tuple[int | None, int | None]:
    try:
        client = redis.from_url(os.environ["REDIS_URL"])
        stage0 = JobQueue.for_stage(
            client, QueueStage.raw2docling_raw, config.queues.raw2docling_raw
        ).depth()
        stage1 = JobQueue.for_stage(
            client,
            QueueStage.docling_raw2docling_clean00,
            config.queues.docling_raw2docling_clean00,
        ).depth()
        return stage0, stage1
    except Exception:
        return None, None


def _redis_row(now: datetime) -> ServiceRow:
    try:
        redis.from_url(os.environ["REDIS_URL"]).ping()
        return ServiceRow(component="redis", status="UP", details="ping ok", updated_at=now)
    except Exception as exc:
        return ServiceRow(component="redis", status="DOWN", details=str(exc), updated_at=now)


def _shared_row(config: AppConfig, now: datetime) -> ServiceRow:
    path = Path(config.shared_root)
    if path.is_dir() and os.access(path, os.W_OK):
        return ServiceRow(component="SHARED", status="UP", details=str(path), updated_at=now)
    return ServiceRow(
        component="SHARED",
        status="DOWN",
        details="missing or not writable",
        updated_at=now,
    )


def _count_lock_files(shared_root: str) -> int:
    root = Path(shared_root)
    if not root.is_dir():
        return 0
    count = 0
    for path in root.rglob("*"):
        if path.is_file() and (
            path.name.endswith(".upload.lock") or path.name.endswith(".worker.lock")
        ):
            count += 1
    return count
