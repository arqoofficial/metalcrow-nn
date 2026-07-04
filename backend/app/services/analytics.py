"""Аналитика пробелов и KPI (SPEC_V3 §2/§5.4/§8.7).

TODO(SPEC_V3 §5.4): пороги regime bucket должны читаться из
`dictionaries/regime_buckets.yaml`, сейчас захардкожены по §4 (low <400°C,
medium 400-800°C, high >800°C). TODO(§2): `avg_response_time_seconds`/
`entity_extraction_f1`/`graph_coverage` — требуют инструментирования вне API
(замер latency, ручная разметка hold-out, graph coverage script) — пока `null`.
"""

from sqlalchemy import text
from sqlmodel import Session

from app.schemas.analytics import (
    CoverageCell,
    CoverageResponse,
    MetricsResponse,
)
from app.schemas.common import RegimeBucket

_REGIME_BUCKET_CASE = """
    CASE
        WHEN temperature IS NULL THEN NULL
        WHEN temperature < 400 THEN 'low'
        WHEN temperature <= 800 THEN 'medium'
        ELSE 'high'
    END
"""


def _coverage_grid(
    session: Session,
) -> tuple[list[str], list[str], dict[tuple[str, str, str], int]]:
    materials = [
        row[0]
        for row in session.execute(
            text(
                "SELECT DISTINCT material_name FROM experiments.experiments_flat "
                "WHERE material_name IS NOT NULL ORDER BY 1"
            )
        ).all()
    ]
    properties = [
        row[0]
        for row in session.execute(
            text(
                "SELECT DISTINCT property_name FROM experiments.experiments_flat "
                "WHERE property_name IS NOT NULL ORDER BY 1"
            )
        ).all()
    ]
    rows = (
        session.execute(
            text(
                f"SELECT material_name, property_name, {_REGIME_BUCKET_CASE} AS bucket, "
                "count(*) AS experiment_count FROM experiments.experiments_flat "
                "WHERE material_name IS NOT NULL AND property_name IS NOT NULL "
                "GROUP BY material_name, property_name, bucket"
            )
        )
        .mappings()
        .all()
    )
    counts = {
        (row["material_name"], row["property_name"], row["bucket"]): row[
            "experiment_count"
        ]
        for row in rows
        if row["bucket"] is not None
    }
    return materials, properties, counts


def coverage(session: Session) -> CoverageResponse:
    materials, properties, counts = _coverage_grid(session)
    buckets = list(RegimeBucket)
    cells = [
        CoverageCell(
            material=material,
            property=property_name,
            regime_bucket=bucket,
            experiment_count=counts.get((material, property_name, bucket.value), 0),
        )
        for material in materials
        for property_name in properties
        for bucket in buckets
    ]
    return CoverageResponse(
        cells=cells, materials=materials, properties=properties, regime_buckets=buckets
    )


def metrics(session: Session) -> MetricsResponse:
    total_experiments = session.execute(
        text("SELECT count(*) FROM experiments.experiments")
    ).scalar_one()
    total_documents = session.execute(
        text("SELECT count(*) FROM experiments.documents")
    ).scalar_one()
    total_materials = session.execute(
        text("SELECT count(*) FROM experiments.materials")
    ).scalar_one()
    return MetricsResponse(
        total_experiments=total_experiments,
        total_documents=total_documents,
        total_materials=total_materials,
    )
