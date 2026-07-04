from fastapi import APIRouter, Depends

from app.api.deps import SessionDep, get_current_user
from app.schemas.analytics import CoverageResponse, MetricsResponse
from app.services import analytics as analytics_service

router = APIRouter(
    prefix="/analytics", tags=["analytics"], dependencies=[Depends(get_current_user)]
)
# GET /api/v1/metrics живёт вне /analytics по §8.7 — отдельный роутер с тем же тегом.
metrics_router = APIRouter(tags=["analytics"], dependencies=[Depends(get_current_user)])


@router.get("/coverage", response_model=CoverageResponse)
def coverage(session: SessionDep) -> CoverageResponse:
    """GET /api/v1/analytics/coverage — полная heatmap (заполненные + пустые ячейки)."""
    return analytics_service.coverage(session)


@metrics_router.get("/metrics", response_model=MetricsResponse)
def metrics(session: SessionDep) -> MetricsResponse:
    """GET /api/v1/metrics — KPI dashboard (SPEC_V3 §2)."""
    return analytics_service.metrics(session)
