import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.schemas.graph import PathResponse, SubgraphResponse
from app.services import graph as graph_service
from tests.utils.experiment import create_full_experiment


def test_graph_requires_auth(client: TestClient) -> None:
    assert (
        client.post(
            f"{settings.API_V1_STR}/graph/query",
            json={"template_id": "x"},
        ).status_code
        == 401
    )
    assert (
        client.get(f"{settings.API_V1_STR}/graph/subgraph/{uuid.uuid4()}").status_code
        == 401
    )
    assert (
        client.get(
            f"{settings.API_V1_STR}/graph/path", params={"from": "a", "to": "b"}
        ).status_code
        == 401
    )


def test_graph_query_invalid_max_depth(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/graph/query",
        headers=normal_user_token_headers,
        json={"template_id": "shortest_path", "max_depth": 6},
    )
    assert r.status_code == 422


def test_graph_query_missing_template_id(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/graph/query",
        headers=normal_user_token_headers,
        json={},
    )
    assert r.status_code == 422


def test_graph_query_stub_empty_result(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/graph/query",
        headers=normal_user_token_headers,
        json={"template_id": "material_neighbourhood", "params": {"material": "Ti"}},
    )
    assert r.status_code == 200
    body = SubgraphResponse.model_validate(r.json())
    assert body.nodes == []
    assert body.edges == []


def test_graph_subgraph_unknown_entity(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.get(
        f"{settings.API_V1_STR}/graph/subgraph/{uuid.uuid4()}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 200
    body = SubgraphResponse.model_validate(r.json())
    assert body.nodes == []
    assert body.edges == []


def test_graph_subgraph_invalid_depth(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.get(
        f"{settings.API_V1_STR}/graph/subgraph/{uuid.uuid4()}",
        headers=normal_user_token_headers,
        params={"depth": 0},
    )
    assert r.status_code == 422


def test_graph_subgraph_happy_path(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    experiment = create_full_experiment(db)

    r = client.get(
        f"{settings.API_V1_STR}/graph/subgraph/{experiment.id}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 200
    body = SubgraphResponse.model_validate(r.json())
    assert any(node.id == str(experiment.id) for node in body.nodes)
    assert len(body.nodes) > 1
    assert len(body.edges) > 0


def test_graph_path(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    """Neo4j опционален (SPEC_V3 §3 п.8): недоступен -> 503 без SQL fallback (§8.3 P2),
    доступен -> 200 с пустым путём-заглушкой (шаблоны `shortestPath` — TODO)."""
    r = client.get(
        f"{settings.API_V1_STR}/graph/path",
        headers=normal_user_token_headers,
        params={"from": "a", "to": "b"},
    )
    if graph_service.neo4j_available():
        assert r.status_code == 200
        PathResponse.model_validate(r.json())
    else:
        assert r.status_code == 503


def test_graph_path_invalid_max_depth(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.get(
        f"{settings.API_V1_STR}/graph/path",
        headers=normal_user_token_headers,
        params={"from": "a", "to": "b", "max_depth": 10},
    )
    assert r.status_code == 422


def test_graph_path_missing_params(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.get(
        f"{settings.API_V1_STR}/graph/path",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 422
