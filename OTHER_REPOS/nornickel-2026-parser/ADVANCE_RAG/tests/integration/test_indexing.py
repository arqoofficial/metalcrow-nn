"""Indexing endpoint integration tests."""

import time

from fastapi.testclient import TestClient


def test_index_doc_valid_path(test_app) -> None:
    app, _, shared_tree, _ = test_app
    client = TestClient(app)
    response = client.post(
        "/api/v1/index_doc",
        json={"path": "01_docling_clean00/reports/q1_report.okf.md"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "indexed"
    assert "01_docling_clean00" in body["path"]


def test_index_doc_invalid_path(test_app) -> None:
    app, _, _, _ = test_app
    client = TestClient(app)
    response = client.post(
        "/api/v1/index_doc",
        json={"path": "01_docling_clean00/reports/missing.okf.md"},
    )
    assert response.status_code == 404


def test_index_doc_disallowed_subfolder(test_app) -> None:
    app, _, _, _ = test_app
    client = TestClient(app)
    response = client.post(
        "/api/v1/index_doc",
        json={"path": "forbidden/doc.okf.md"},
    )
    assert response.status_code == 400


def test_index_path_async_flow(test_app) -> None:
    app, _, shared_tree, _ = test_app
    client = TestClient(app)
    response = client.post(
        "/api/v1/index_path",
        json={"path": "01_docling_clean00/reports"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["job_id"]
    assert body["path"] == "01_docling_clean00/reports"

    time.sleep(2)
    query = client.post(
        "/api/v1/query",
        json={"query": "nickel production", "type": "dense"},
    )
    assert query.status_code == 200
    assert len(query.json()["results"]) >= 1


def test_index_path_enqueues_job_with_correlation_id(test_app) -> None:
    app, _, _, _ = test_app
    state = app.state.app_state
    if state.worker is not None:
        state.worker.stop()
    client = TestClient(app)
    response = client.post(
        "/api/v1/index_path",
        json={"path": "01_docling_clean00/reports"},
    )
    assert response.status_code == 202
    item = state.queue.dequeue()
    assert item is not None
    _, job = item
    assert job.correlation_id


def test_index_path_accepts_source_subfolder_root(test_app) -> None:
    app, _, _, _ = test_app
    client = TestClient(app)
    response = client.post(
        "/api/v1/index_path",
        json={"path": "01_docling_clean00"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["path"] == "01_docling_clean00"


def test_index_path_rejects_file_path(test_app) -> None:
    app, _, _, _ = test_app
    client = TestClient(app)
    response = client.post(
        "/api/v1/index_path",
        json={"path": "01_docling_clean00/reports/q1_report.okf.md"},
    )
    assert response.status_code == 400
