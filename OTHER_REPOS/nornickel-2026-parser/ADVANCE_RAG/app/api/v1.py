"""API v1 routers."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import (
    IndexDocRequest,
    IndexDocResponse,
    IndexPathRequest,
    IndexPathResponse,
    QueryRequest,
    QueryResponse,
)
from app.data.okf import OkfParseError
from app.data.paths import PathValidationError
from app.indexing.service import IndexingService
from app.observability.logging import bind_request_context
from app.observability.metrics import (
    INDEX_DOC_REQUESTS,
    INDEX_PATH_JOBS,
    QUERY_LATENCY,
    QUERY_REQUESTS,
    metrics_enabled,
    new_correlation_id,
    span,
)
from app.queue.jobs import IndexPathJob
from app.retrieval.query_service import QueryService

router = APIRouter(prefix="/api/v1", tags=["v1"])


def _get_services(request: Request) -> tuple[QueryService, IndexingService]:
    state = request.app.state.app_state
    base_dir = request.app.state.base_dir
    query_service = QueryService(state.runtime, state.chroma, base_dir)
    indexing_service = IndexingService(state.runtime, state.chroma, base_dir)
    return query_service, indexing_service


@router.post("/query", response_model=QueryResponse)
def query_endpoint(payload: QueryRequest, request: Request) -> QueryResponse:
    if not request.app.state.app_state.chroma_ready:
        raise HTTPException(status_code=503, detail="Chroma unavailable")
    bind_request_context(new_correlation_id())
    search_type = payload.effective_type(request.app.state.app_state.runtime.query).value
    if metrics_enabled():
        QUERY_REQUESTS.labels(search_type=search_type).inc()
    start = time.perf_counter()
    with span("query", search_type=search_type):
        service, _ = _get_services(request)
        result = service.execute(payload)
    if metrics_enabled():
        QUERY_LATENCY.labels(search_type=search_type).observe(time.perf_counter() - start)
    if isinstance(result, PathValidationError):
        raise HTTPException(status_code=400, detail=result.message)
    return result


@router.post("/index_doc", response_model=IndexDocResponse)
def index_doc_endpoint(payload: IndexDocRequest, request: Request) -> IndexDocResponse:
    if not request.app.state.app_state.chroma_ready:
        raise HTTPException(status_code=503, detail="Chroma unavailable")
    bind_request_context(new_correlation_id())
    if metrics_enabled():
        INDEX_DOC_REQUESTS.inc()
    with span("index_doc"):
        _, indexing = _get_services(request)
        result = indexing.index_document(payload.path)
    if isinstance(result, PathValidationError):
        http_status = 404 if result.code == "not_found" else 400
        raise HTTPException(status_code=http_status, detail=result.message)
    if isinstance(result, OkfParseError):
        raise HTTPException(status_code=400, detail=result.message)
    index_status, path, _ = result
    return IndexDocResponse(status=index_status, path=path)


@router.post("/index_path", response_model=IndexPathResponse, status_code=202)
def index_path_endpoint(payload: IndexPathRequest, request: Request) -> IndexPathResponse:
    state = request.app.state.app_state
    if not state.chroma_ready:
        raise HTTPException(status_code=503, detail="Chroma unavailable")
    _, indexing = _get_services(request)
    resolved = indexing.resolve_index_target(payload.path)
    if isinstance(resolved, PathValidationError):
        raise HTTPException(status_code=400, detail=resolved.message)
    if not resolved.absolute.is_dir():
        if resolved.absolute.is_file():
            detail = "Path must reference a folder, not a file"
        elif not resolved.absolute.exists():
            detail = f"Folder not found: {payload.path}"
        else:
            detail = "Path must reference a folder under SHARED"
        raise HTTPException(status_code=400, detail=detail)
    queue = state.queue
    if queue is None:
        raise HTTPException(status_code=503, detail="Queue unavailable")
    correlation_id = new_correlation_id()
    bind_request_context(correlation_id)
    job = IndexPathJob(
        subfolder_path=resolved.relative_in_subfolder,
        source_subfolder=resolved.source_subfolder,
        correlation_id=correlation_id,
    )
    job_id = queue.enqueue(job)
    if metrics_enabled():
        INDEX_PATH_JOBS.inc()
    queue_size = queue.size()
    with span(
        "index_path",
        job_id=job_id,
        source_subfolder=resolved.source_subfolder,
        subfolder_path=resolved.relative_in_subfolder,
        queue_size=str(queue_size),
        correlation_id=correlation_id,
    ):
        response = IndexPathResponse(
            status="accepted",
            job_id=job_id,
            path=resolved.relative_to_shared,
        )
    return response
