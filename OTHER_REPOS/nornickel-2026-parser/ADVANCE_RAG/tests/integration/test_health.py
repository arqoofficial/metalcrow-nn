"""Health and metrics integration tests."""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.config.settings import clear_settings_cache
from app.main import create_app


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    data = {
        "api": {"version": "v1", "host": "0.0.0.0", "port": 8114},
        "shared": {"root": str(tmp_path / "SHARED")},
        "query": {
            "default_type": "advance",
            "default_limit": 10,
            "default_source_subfolder": "01_docling_clean00",
            "allowed_source_subfolders": ["00_docling_raw", "01_docling_clean00"],
            "preprocessing": {"lemmatization": True, "stemming": True, "languages": ["en", "ru"]},
        },
        "chroma": {
            "mode": "cpu_local",
            "persist_directory": str(tmp_path / "chroma"),
            "collection_name": "test_health",
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    app = create_app(path, tmp_path)
    return TestClient(app)


def test_health_returns_200(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_returns_200_when_dependencies_reachable(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["config_loaded"] is True
    assert body["chroma_ready"] is True


def test_metrics_returns_prometheus_payload(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert b"python_info" in response.content or len(response.content) > 0
