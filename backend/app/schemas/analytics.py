from sqlmodel import Field, SQLModel

from app.schemas.common import RegimeBucket


class CoverageCell(SQLModel):
    material: str
    property: str
    regime_bucket: RegimeBucket
    experiment_count: int


# GET /api/v1/analytics/coverage response — полная heatmap (заполненные + пустые ячейки)
class CoverageResponse(SQLModel):
    cells: list[CoverageCell]
    materials: list[str]
    properties: list[str]
    regime_buckets: list[RegimeBucket]


# GET /api/v1/metrics response — KPI dashboard (SPEC_V3 §2)
class MetricsResponse(SQLModel):
    total_experiments: int
    total_documents: int
    total_materials: int
    avg_response_time_seconds: float | None = None  # TODO(§2): замер online-режима
    entity_extraction_f1: float | None = None  # TODO(§2): ручная разметка hold-out
    provenance_coverage: float = Field(default=1.0, ge=0, le=1)
    graph_coverage: float | None = Field(default=None, ge=0, le=1)  # TODO(§2)
