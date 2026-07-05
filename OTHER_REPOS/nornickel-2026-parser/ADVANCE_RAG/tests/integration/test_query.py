"""Query endpoint integration tests."""


import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(test_app):
    app, tmp_path, shared_tree, _ = test_app
    return TestClient(app), shared_tree, tmp_path


def _index_sample(client: TestClient, shared_tree) -> None:
    response = client.post(
        "/api/v1/index_doc",
        json={"path": "01_docling_clean00/reports/q1_report.okf.md"},
    )
    assert response.status_code == 200, response.text


def test_dense_query_returns_ranked_results(client) -> None:
    test_client, shared_tree, _ = client
    _index_sample(test_client, shared_tree)
    response = test_client.post(
        "/api/v1/query",
        json={"query": "nickel production forecast", "type": "dense", "limit": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "dense"
    assert len(body["results"]) >= 1
    assert body["results"][0]["okf_meta"]["type"] == "report"


def test_no_match_returns_200_with_empty_results(client) -> None:
    test_client, _, _ = client
    response = test_client.post(
        "/api/v1/query",
        json={"query": "xyznonexistentterm999", "type": "dense"},
    )
    assert response.status_code == 200
    assert response.json()["results"] == []


def test_default_type_when_omitted(client) -> None:
    test_client, shared_tree, _ = client
    _index_sample(test_client, shared_tree)
    response = test_client.post(
        "/api/v1/query",
        json={"query": "nickel forecast"},
    )
    assert response.status_code == 200
    assert response.json()["type"] == "advance"


def test_sparse_query(client) -> None:
    test_client, shared_tree, _ = client
    _index_sample(test_client, shared_tree)
    response = test_client.post(
        "/api/v1/query",
        json={"query": "nickel forecast", "type": "sparse"},
    )
    assert response.status_code == 200
    assert response.json()["type"] == "sparse"


def test_fuzzy_query(client) -> None:
    test_client, shared_tree, _ = client
    _index_sample(test_client, shared_tree)
    response = test_client.post(
        "/api/v1/query",
        json={"query": "nickel forcast", "type": "fuzzy"},
    )
    assert response.status_code == 200
    assert response.json()["type"] == "fuzzy"


def test_rrf_query(client) -> None:
    test_client, shared_tree, _ = client
    _index_sample(test_client, shared_tree)
    response = test_client.post(
        "/api/v1/query",
        json={"query": "nickel forecast", "type": "RRF"},
    )
    assert response.status_code == 200
    assert response.json()["type"] == "RRF"


def test_advance_query(client) -> None:
    test_client, shared_tree, _ = client
    _index_sample(test_client, shared_tree)
    response = test_client.post(
        "/api/v1/query",
        json={"query": "nickel forecast", "type": "advance"},
    )
    assert response.status_code == 200
    assert response.json()["type"] == "advance"


def test_russian_query(client) -> None:
    test_client, shared_tree, tmp_path = client
    test_client.post(
        "/api/v1/index_doc",
        json={"path": "01_docling_clean00/reports/ru_report.okf.md"},
    )
    response = test_client.post(
        "/api/v1/query",
        json={"query": "прогноз производства никеля", "type": "dense"},
    )
    assert response.status_code == 200


def test_disallowed_subfolder_rejected(client) -> None:
    test_client, _, _ = client
    response = test_client.post(
        "/api/v1/query",
        json={"query": "test", "source_subfolder": "forbidden_folder"},
    )
    assert response.status_code == 400
