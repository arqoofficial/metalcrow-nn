"""Operator runtime visibility endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.schemas import AdminRuntimeResponse, ChromaIndexInfo, QueueRuntimeInfo
from app.data.chroma_adapter import describe_dense_embedding

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/runtime", response_model=AdminRuntimeResponse)
def admin_runtime(request: Request) -> AdminRuntimeResponse:
    state = request.app.state.app_state
    queue = state.queue
    if queue is None:
        queue_info = QueueRuntimeInfo(backend="unavailable", size=0, failed_count=0)
    else:
        queue_info = QueueRuntimeInfo(
            backend=state.runtime.queue.backend,
            size=queue.size(),
            failed_count=len(queue.failed_jobs()),
        )

    collection_name = state.runtime.chroma.collection_name
    if not state.chroma_ready or state.chroma is None:
        chroma_info = ChromaIndexInfo(
            ready=False,
            collection_name=collection_name,
            document_count=0,
        )
    else:
        chroma_info = ChromaIndexInfo(
            ready=True,
            collection_name=collection_name,
            document_count=state.chroma.document_count(),
        )

    return AdminRuntimeResponse(
        queue=queue_info,
        chroma=chroma_info,
        dense_embedding=describe_dense_embedding(state.runtime.chroma),
    )
