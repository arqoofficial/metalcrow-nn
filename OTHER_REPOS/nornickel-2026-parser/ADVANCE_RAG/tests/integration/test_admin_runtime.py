"""Admin runtime endpoint integration tests."""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.config.settings import clear_settings_cache
from app.main import create_app
from app.queue.jobs import IndexPathJob, JobQueue


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
            "collection_name": "test_admin_runtime",
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    app = create_app(path, tmp_path)
    return TestClient(app)


def test_admin_runtime_reports_queue_and_embedding(client: TestClient) -> None:
    queue = client.app.state.app_state.queue
    assert isinstance(queue, JobQueue)
    queue.enqueue(
        IndexPathJob(
            subfolder_path="reports",
            source_subfolder="01_docling_clean00",
            correlation_id="test-correlation",
        )
    )
    queue.record_failure(
        "job-failed",
        IndexPathJob(
            subfolder_path="reports",
            source_subfolder="01_docling_clean00",
            correlation_id="test-correlation",
        ),
        "boom",
    )

    response = client.get("/admin/runtime")
    assert response.status_code == 200
    body = response.json()
    assert body["queue"]["backend"] == "memory"
    assert body["queue"]["size"] == 1
    assert body["queue"]["failed_count"] == 1
    assert body["chroma"]["ready"] is True
    assert body["chroma"]["collection_name"] == "test_admin_runtime"
    assert body["chroma"]["document_count"] == 0
    assert body["dense_embedding"]["mode"] == "cpu_local"
    assert body["dense_embedding"]["model"] == "all-MiniLM-L6-v2"
    assert body["dense_embedding"]["provider"] == "chromadb_onnx"


def test_admin_runtime_reports_indexed_document_count(client: TestClient) -> None:
    chroma = client.app.state.app_state.chroma
    assert chroma is not None
    chroma.upsert("doc-1", "Nickel forecast content", {"path": "reports/q1.okf.md"})

    response = client.get("/admin/runtime")
    assert response.status_code == 200
    assert response.json()["chroma"]["document_count"] == 1
