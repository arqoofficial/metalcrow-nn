"""Root-level health probes (outside /api/v1)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.presentation.openapi_meta import TAG_HEALTH
from app.presentation.schemas import HealthResponse, ReadyResponse

router = APIRouter(tags=[TAG_HEALTH])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns `200` when the API process is running. Does not check Redis or disk.",
)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness probe",
    description="Returns `200` when Redis responds to `PING`. Used by the admin panel and orchestrators.",
    responses={503: {"description": "Redis unreachable or not configured"}},
)
def ready(request: Request) -> ReadyResponse:
    client = request.app.state.redis
    client.ping()
    return ReadyResponse(status="ready")
