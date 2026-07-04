"""HTTP client for the nornickel-2026-parser service (adapter for the L1 slot)."""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import Enum

import httpx
from pydantic import BaseModel

from parse_docling.config import settings


class ParserError(RuntimeError):
    """Raised when the parser rejects a request or a stage fails."""


class ProcessingStatus(str, Enum):
    pending = "pending"
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class ProcessResponse(BaseModel):
    requested_path: str
    resolved_path: str
    enforce: bool
    status: ProcessingStatus


class StageStatus(BaseModel):
    stage: str
    status: ProcessingStatus
    okf_path: str | None = None


class FileStatusResponse(BaseModel):
    requested_path: str
    resolved_path: str
    overall_status: ProcessingStatus
    stages: list[StageStatus]


_STAGE0 = "docling_raw"
_STAGE1 = "docling_clean00"

ProgressCallback = Callable[[float], None]

_WAIT_PHASE_START = 0.12
_WAIT_PHASE_END = 0.95


def poll_wait_fraction(
    elapsed_s: float,
    timeout_s: float,
    stage_status: ProcessingStatus | None,
) -> float:
    """Map polling state to 0..1 within the Docling wait phase."""
    if timeout_s <= 0:
        return 1.0
    time_frac = min(elapsed_s / timeout_s, 0.98)
    if stage_status == ProcessingStatus.done:
        return 1.0
    if stage_status == ProcessingStatus.processing:
        return max(0.35, time_frac)
    if stage_status in (ProcessingStatus.queued, ProcessingStatus.pending):
        return min(time_frac, 0.3)
    return time_frac


def _client() -> httpx.Client:
    return httpx.Client(base_url=settings.PARSER_URL, timeout=settings.PARSER_TIMEOUT_S)


def enqueue_process(resolved_path: str) -> ProcessResponse:
    """POST /api/v1/files/process — enqueue stage-0 Docling job."""
    deadline = time.monotonic() + settings.PARSER_UPLOAD_WAIT_S
    with _client() as client:
        while True:
            resp = client.post(
                "/api/v1/files/process",
                json={"path": resolved_path, "enforce": settings.PARSER_ENFORCE},
            )
            if resp.status_code == 404 and time.monotonic() < deadline:
                time.sleep(settings.PARSER_POLL_INTERVAL_S)
                continue
            break
    if resp.status_code >= 400:
        raise ParserError(f"process failed ({resp.status_code}): {resp.text}")
    return ProcessResponse.model_validate(resp.json())


def get_status(resolved_path: str) -> FileStatusResponse:
    """GET /api/v1/files/status — per-stage pipeline status for a file."""
    with _client() as client:
        resp = client.get("/api/v1/files/status", params={"path": resolved_path})
    if resp.status_code >= 400:
        raise ParserError(f"status failed ({resp.status_code}): {resp.text}")
    return FileStatusResponse.model_validate(resp.json())


def _stage_status(status: FileStatusResponse, stage_id: str) -> StageStatus | None:
    return next((s for s in status.stages if s.stage == stage_id), None)


def wait_until_done(
    resolved_path: str,
    *,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Poll until stage-1 (`docling_clean00`) is done; fallback to stage-0 OKF path."""
    started = time.monotonic()
    deadline = started + settings.PARSER_POLL_TIMEOUT_S
    timeout_s = settings.PARSER_POLL_TIMEOUT_S
    while True:
        status = get_status(resolved_path)
        stage1 = _stage_status(status, _STAGE1)
        stage0 = _stage_status(status, _STAGE0)
        if stage0 is not None and stage0.status == ProcessingStatus.failed:
            raise ParserError(f"stage-0 failed for {resolved_path}")
        if stage1 is not None and stage1.status == ProcessingStatus.failed:
            raise ParserError(f"stage-1 failed for {resolved_path}")
        active = stage1 or stage0
        stage_status = active.status if active is not None else None
        elapsed = time.monotonic() - started
        if on_progress is not None:
            wait_frac = poll_wait_fraction(elapsed, timeout_s, stage_status)
            on_progress(_WAIT_PHASE_START + wait_frac * (_WAIT_PHASE_END - _WAIT_PHASE_START))
        if stage1 is not None:
            if stage1.status == ProcessingStatus.done and stage1.okf_path:
                if on_progress is not None:
                    on_progress(_WAIT_PHASE_END)
                return stage1.okf_path
        elif stage0 is not None:
            if stage0.status == ProcessingStatus.done and stage0.okf_path:
                if on_progress is not None:
                    on_progress(_WAIT_PHASE_END)
                return stage0.okf_path
        if time.monotonic() >= deadline:
            raise ParserError(f"pipeline timed out for {resolved_path}")
        time.sleep(settings.PARSER_POLL_INTERVAL_S)
