"""Health, readiness, and metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["observability"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> dict[str, str | bool]:
    state = request.app.state.app_state
    config_ok = state.runtime is not None
    chroma_ok = getattr(state, "chroma_ready", False)
    ready_status = config_ok and chroma_ok
    return {
        "status": "ready" if ready_status else "not_ready",
        "config_loaded": config_ok,
        "chroma_ready": chroma_ok,
    }


@router.get("/metrics")
def metrics(request: Request) -> Response:
    if not request.app.state.app_state.runtime.observability.metrics_enabled:
        return Response(status_code=404)
    payload = generate_latest()
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
