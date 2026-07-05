"""FastAPI routes for parser API."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Path as PathParam, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse

from app.config.models import AppConfig
from app.locks.files import create_upload_lock, remove_lock, upload_lock_path
from app.paths import PathValidationError
from app.presentation.openapi_meta import (
    TAG_BROWSE,
    TAG_CONTENT,
    TAG_FILES,
    TAG_INTERNAL,
    TAG_OPERATIONS,
    TAG_STATISTICS,
)
from app.presentation.schemas import (
    ErrorResponse,
    FileStatusResponse,
    FileTreeResponse,
    ProcessRequest,
    ProcessResponse,
    ProcessingStatus,
    ReindexRequest,
    ReindexResponse,
    StatisticsResponse,
    UploadResponse,
)
from app.queue.job import QueueJob, QueueStage
from app.queue.redis_queue import JobQueue
from app.services.path_resolution import (
    allocate_upload_resolved_path,
    reject_concrete_upload_path,
    resolve_exact_okf_path,
    resolve_exact_raw_path,
    validate_selector_consistency,
)
from app.services.reindex_service import enqueue_reindex
from app.services.statistics_service import build_statistics
from app.services.status_service import build_file_status, stage0_output_exists
from app.presentation.tree import TreeValidationError, build_tree_response

router = APIRouter(prefix="/api/v1")

_COMMON_ERRORS: dict[int, dict[str, type[ErrorResponse] | str]] = {
    400: {"model": ErrorResponse, "description": "Invalid path or parameters"},
    404: {"model": ErrorResponse, "description": "Resource not found on disk"},
    409: {"model": ErrorResponse, "description": "Conflict with existing pipeline output"},
    422: {"model": ErrorResponse, "description": "Request validation error"},
    500: {"model": ErrorResponse, "description": "Internal server error"},
}


def _config(request: Request) -> AppConfig:
    return request.app.state.config


def _stage0_queue(request: Request) -> JobQueue:
    return request.app.state.stage0_queue


def _stage1_queue(request: Request) -> JobQueue:
    return request.app.state.stage1_queue


def _write_upload(
    *,
    shared_root: str,
    resolved_path: str,
    upload_suffix: str,
    payload: bytes,
) -> None:
    lock_path = upload_lock_path(shared_root, resolved_path, upload_suffix)
    target_path = Path(shared_root) / resolved_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    create_upload_lock(lock_path)
    try:
        target_path.write_bytes(payload)
    finally:
        remove_lock(lock_path)


@router.post(
    "/files/upload",
    status_code=202,
    response_model=UploadResponse,
    tags=[TAG_FILES],
    summary="Upload a raw file",
    description=(
        "Accepts a logical path and file body. First upload writes a simple filename; "
        "repeat uploads allocate the next `__vNN` suffix. Write happens in a background task "
        "after `202 Accepted`. Archives are rejected with `400`."
    ),
    responses={400: _COMMON_ERRORS[400], 422: _COMMON_ERRORS[422]},
)
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Raw document bytes (PDF, Office, HTML, …)."),
    path: str = Form(..., description="Logical path without source prefix, e.g. `reports/q1.pdf`."),
) -> UploadResponse:
    config = _config(request)
    try:
        reject_concrete_upload_path(path)
        resolved_path = allocate_upload_resolved_path(config.shared_root, path)
    except PathValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = await file.read()
    background_tasks.add_task(
        _write_upload,
        shared_root=config.shared_root,
        resolved_path=resolved_path,
        upload_suffix=config.locks.upload_suffix,
        payload=payload,
    )
    is_final = (Path(config.shared_root) / resolved_path).is_file()
    return UploadResponse(
        requested_path=path,
        resolved_path=resolved_path,
        is_final=is_final,
    )


@router.post(
    "/files/process",
    status_code=202,
    response_model=ProcessResponse,
    tags=[TAG_FILES],
    summary="Enqueue pipeline processing",
    description=(
        "Resolves the exact raw file on disk and enqueues a stage-0 Docling job. "
        "Returns `409` when stage-0 OKF already exists and `enforce=false`. "
        "Duplicate jobs are allowed while output is not yet on disk."
    ),
    responses={
        400: _COMMON_ERRORS[400],
        404: _COMMON_ERRORS[404],
        409: _COMMON_ERRORS[409],
        422: _COMMON_ERRORS[422],
    },
)
def process_file(request: Request, body: ProcessRequest) -> ProcessResponse:
    config = _config(request)
    stage0_queue = _stage0_queue(request)
    try:
        resolved = resolve_exact_raw_path(body.path, config.shared_root)
    except PathValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if resolved is None:
        raise HTTPException(status_code=404, detail="file not found")

    if stage0_output_exists(config.shared_root, resolved.relative) and not body.enforce:
        raise HTTPException(
            status_code=409,
            detail="stage-0 output already exists; pass enforce=true to reprocess",
        )

    queued_at = datetime.now(timezone.utc)
    stage0_queue.enqueue(
        QueueJob(
            requested_path=body.path,
            resolved_path=resolved.relative,
            stage=QueueStage.raw2docling_raw,
            enforce=body.enforce,
            enqueued_at=queued_at,
        )
    )
    return ProcessResponse(
        requested_path=body.path,
        resolved_path=resolved.relative,
        enforce=body.enforce,
        status=ProcessingStatus.queued,
        queued_at=queued_at,
    )


@router.get(
    "/files/status",
    response_model=FileStatusResponse,
    tags=[TAG_FILES],
    summary="Get per-file pipeline status",
    description=(
        "Derives stage statuses from OKF files on disk, Redis queue membership, "
        "worker locks, and `.pipeline_errors/` markers."
    ),
    responses={400: _COMMON_ERRORS[400], 404: _COMMON_ERRORS[404]},
)
def file_status(
    request: Request,
    path: str = Query(
        ...,
        description="Logical or concrete raw/OKF path.",
        examples=["reports/q1.pdf", "RAW_DATA/reports/q1.pdf"],
    ),
) -> FileStatusResponse:
    config = _config(request)
    try:
        return build_file_status(
            config,
            path,
            _stage0_queue(request),
            _stage1_queue(request),
        )
    except PathValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/files/tree",
    response_model=FileTreeResponse,
    tags=[TAG_BROWSE],
    summary="Browse SHARED storage tree",
    description=(
        "Returns a paginated tree rooted at `SHARED/` or a relative subtree. "
        "Hidden dotfiles and lock files are excluded; symlinks are not followed."
    ),
    responses={400: _COMMON_ERRORS[400], 404: _COMMON_ERRORS[404]},
)
def files_tree(
    request: Request,
    root: str = Query("", description="Subtree relative to `SHARED/`, e.g. `RAW_DATA` or empty for root."),
    max_depth: int = Query(6, description="Maximum nesting depth to include (0–10)."),
    include_files: bool = Query(True, description="Include file nodes."),
    include_dirs: bool = Query(True, description="Include directory nodes."),
    offset: int = Query(0, description="Pagination offset for direct children of resolved root (≥ 0)."),
    limit: int = Query(200, description="Max direct children returned at resolved root (1–1000)."),
) -> FileTreeResponse:
    try:
        return build_tree_response(
            shared_root=_config(request).shared_root,
            root=root,
            max_depth=max_depth,
            include_files=include_files,
            include_dirs=include_dirs,
            offset=offset,
            limit=limit,
        )
    except TreeValidationError as exc:
        raise HTTPException(status_code=exc.code, detail=str(exc)) from exc


@router.get(
    "/markdown",
    tags=[TAG_CONTENT],
    summary="Download OKF markdown",
    description=(
        "Returns UTF-8 markdown for an exact OKF path under `SHARED/`. "
        "Raw paths map to stage-0 OKF for the same resolved file only."
    ),
    responses={
        400: _COMMON_ERRORS[400],
        404: _COMMON_ERRORS[404],
    },
)
def markdown_content(
    request: Request,
    okf_path: str = Query(
        ...,
        description="Logical or concrete OKF path, e.g. `00_docling_raw/RAW_DATA/reports/q1.pdf.md`.",
    ),
) -> PlainTextResponse:
    config = _config(request)
    try:
        resolved_okf = resolve_exact_okf_path(okf_path, config.shared_root)
    except PathValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if resolved_okf is None:
        raise HTTPException(status_code=404, detail="markdown not found")

    target = Path(config.shared_root) / resolved_okf
    return PlainTextResponse(
        content=target.read_text(encoding="utf-8"),
        media_type="text/markdown",
        headers={
            "X-Requested-Path": okf_path,
            "X-Resolved-Path": resolved_okf,
        },
    )


@router.get(
    "/statistics",
    response_model=StatisticsResponse,
    tags=[TAG_STATISTICS],
    summary="Pipeline coverage statistics",
    description="Counts all Docling-eligible raw files and per-stage OKF outputs under `SHARED/`.",
)
def statistics(request: Request) -> StatisticsResponse:
    return build_statistics(_config(request).shared_root)


@router.post(
    "/reindex",
    status_code=202,
    response_model=ReindexResponse,
    tags=[TAG_OPERATIONS],
    summary="Bulk reindex",
    description=(
        "Enqueues stage-0 jobs for every Docling-eligible raw file. "
        "Archives and unsupported extensions are skipped. "
        "Use `enforce=true` to reprocess files that already have stage-0 output."
    ),
)
def reindex(request: Request, body: ReindexRequest) -> ReindexResponse:
    stage0_count, stage1_count = enqueue_reindex(
        _config(request),
        _stage0_queue(request),
        _stage1_queue(request),
        enforce=body.enforce,
    )
    return ReindexResponse(enqueued=stage0_count, stage1_enqueued=stage1_count)


@router.get(
    "/health/error/{code}",
    tags=[TAG_INTERNAL],
    summary="Raise sample HTTP error",
    description="Development helper that returns the requested status code for contract testing.",
    responses={
        400: _COMMON_ERRORS[400],
        404: _COMMON_ERRORS[404],
        409: _COMMON_ERRORS[409],
        422: _COMMON_ERRORS[422],
        500: _COMMON_ERRORS[500],
    },
)
def error_contract(
    code: int = PathParam(..., description="HTTP status code to return.", examples=[404, 409]),
) -> None:
    if code == 400:
        raise HTTPException(status_code=400, detail="invalid request")
    if code == 404:
        raise HTTPException(status_code=404, detail="not found")
    if code == 409:
        raise HTTPException(status_code=409, detail="conflict")
    if code == 422:
        raise HTTPException(status_code=422, detail="validation error")
    if code == 500:
        raise HTTPException(status_code=500, detail="internal error")
    raise HTTPException(status_code=400, detail="unknown error code")


@router.get(
    "/validate/path",
    tags=[TAG_INTERNAL],
    summary="Validate path selector",
    description=(
        "Returns `200` when the path is syntactically valid. "
        "Returns `400` for inconsistent version tokens without a source prefix."
    ),
    responses={400: _COMMON_ERRORS[400]},
)
def validate_path(
    path: str = Query(..., description="Path selector to validate.", examples=["reports/q1.pdf"]),
) -> None:
    try:
        validate_selector_consistency(path)
    except PathValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
