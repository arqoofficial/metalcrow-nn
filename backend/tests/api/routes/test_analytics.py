from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.schemas.analytics import CoverageResponse, MetricsResponse
from tests.utils.experiment import create_full_experiment
from tests.utils.utils import random_lower_string


def test_analytics_requires_auth(client: TestClient) -> None:
    assert client.get(f"{settings.API_V1_STR}/analytics/coverage").status_code == 401
    assert client.get(f"{settings.API_V1_STR}/metrics").status_code == 401


def test_coverage_happy_path(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    material_name = f"coverage-material-{random_lower_string()}"
    property_name = f"coverage-property-{random_lower_string()}"
    create_full_experiment(
        db,
        material_name=material_name,
        property_name=property_name,
        temperature=500.0,
    )

    r = client.get(
        f"{settings.API_V1_STR}/analytics/coverage", headers=normal_user_token_headers
    )
    assert r.status_code == 200
    body = CoverageResponse.model_validate(r.json())
    assert material_name in body.materials
    assert property_name in body.properties

    filled = [
        c
        for c in body.cells
        if c.material == material_name
        and c.property == property_name
        and c.regime_bucket == "medium"
    ]
    assert len(filled) == 1
    assert filled[0].experiment_count == 1


def test_metrics_happy_path(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    create_full_experiment(db)

    r = client.get(f"{settings.API_V1_STR}/metrics", headers=normal_user_token_headers)
    assert r.status_code == 200
    body = MetricsResponse.model_validate(r.json())
    assert body.total_experiments >= 1
    assert body.total_documents >= 1
    assert body.total_materials >= 1
