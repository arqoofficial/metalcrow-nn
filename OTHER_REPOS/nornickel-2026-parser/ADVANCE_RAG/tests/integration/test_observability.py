"""Observability integration tests."""

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.main import create_app


def test_metrics_emitted_for_query(test_app) -> None:
    app, _, shared_tree, _ = test_app
    client = TestClient(app)
    client.post(
        "/api/v1/index_doc",
        json={"path": "01_docling_clean00/reports/q1_report.okf.md"},
    )
    client.post("/api/v1/query", json={"query": "nickel", "type": "dense"})
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    body = metrics.text
    assert "advance_rag_query_requests_total" in body


def test_metrics_emitted_for_index_doc(test_app) -> None:
    app, _, _, _ = test_app
    client = TestClient(app)
    client.post(
        "/api/v1/index_doc",
        json={"path": "01_docling_clean00/reports/q1_report.okf.md"},
    )
    metrics = client.get("/metrics")
    assert "advance_rag_index_doc_requests_total" in metrics.text


def test_metrics_emitted_for_index_path(test_app) -> None:
    app, _, _, _ = test_app
    client = TestClient(app)
    client.post(
        "/api/v1/index_path",
        json={"path": "01_docling_clean00/reports"},
    )
    metrics = client.get("/metrics")
    assert "advance_rag_index_path_jobs_total" in metrics.text


def test_opentelemetry_spans_created(test_app) -> None:
    from opentelemetry import trace

    app, _, _, _ = test_app
    client = TestClient(app)
    provider = trace.get_tracer_provider()
    client.post("/api/v1/query", json={"query": "nickel", "type": "dense"})
    assert provider is not None


def test_metrics_endpoint_disabled_returns_404(tmp_path: Path) -> None:
    shared = tmp_path / "SHARED"
    (shared / "01_docling_clean00").mkdir(parents=True)
    data = {
        "api": {"version": "v1", "host": "0.0.0.0", "port": 8114},
        "shared": {"root": str(shared)},
        "query": {
            "default_type": "advance",
            "default_limit": 10,
            "default_source_subfolder": "01_docling_clean00",
            "allowed_source_subfolders": ["01_docling_clean00"],
            "preprocessing": {"lemmatization": True, "stemming": True, "languages": ["en", "ru"]},
        },
        "chroma": {
            "mode": "cpu_local",
            "persist_directory": str(tmp_path / "chroma"),
            "collection_name": "obs_disabled",
        },
        "observability": {"metrics_enabled": False, "tracing_enabled": True, "log_json": True},
    }
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.dump(data), encoding="utf-8")
    app = create_app(cfg, tmp_path)
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 404
