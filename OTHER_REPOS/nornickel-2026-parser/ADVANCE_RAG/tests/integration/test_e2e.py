"""Final end-to-end regression suite."""

import pytest
from fastapi.testclient import TestClient

QUERY_MODES = ["dense", "sparse", "fuzzy", "RRF", "advance"]


@pytest.mark.parametrize("mode", QUERY_MODES)
def test_all_query_modes_return_valid_schema(test_app, mode: str) -> None:
    app, _, shared_tree, _ = test_app
    client = TestClient(app)
    client.post(
        "/api/v1/index_doc",
        json={"path": "01_docling_clean00/reports/q1_report.okf.md"},
    )
    response = client.post("/api/v1/query", json={"query": "nickel forecast", "type": mode})
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == mode
    assert "source_subfolder" in body
    assert "results" in body


def test_no_match_contract_regression(test_app) -> None:
    app, _, _, _ = test_app
    client = TestClient(app)
    for mode in QUERY_MODES:
        response = client.post(
            "/api/v1/query",
            json={"query": "zzznomatch999", "type": mode},
        )
        assert response.status_code == 200
        assert response.json()["results"] == []


def test_default_source_subfolder_regression(test_app) -> None:
    app, _, _, _ = test_app
    client = TestClient(app)
    response = client.post("/api/v1/query", json={"query": "nickel"})
    assert response.status_code == 200
    assert response.json()["source_subfolder"] == "01_docling_clean00"


def test_mcp_sparse_rag_naming_regression() -> None:
    from app.mcp_server import TOOL_TYPE_MAP

    assert "sparse_rag" in TOOL_TYPE_MAP
    assert "sparce_rag" not in TOOL_TYPE_MAP
