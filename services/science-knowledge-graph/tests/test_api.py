"""Integration tests for the FastAPI layer (no real Neo4j — uses mock graph)."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from httpx import AsyncClient, ASGITransport

from fastapi import FastAPI
from science_kg.api.routes import router
from science_kg.models import SearchResult, GraphNode, GraphEdge


@pytest.fixture
def mock_graph():
    graph = AsyncMock()
    graph.bootstrap_schema = AsyncMock()
    graph.upsert_entities = AsyncMock()
    graph.upsert_relations = AsyncMock()
    graph.close = AsyncMock()
    graph.search = AsyncMock(
        return_value=SearchResult(
            nodes=[GraphNode(text="ВТ6", type="MATERIAL", sources=["paper-001"])],
            edges=[
                GraphEdge(
                    source="850°C",
                    target="прочность",
                    relation="produces_output",
                    verb="увеличить",
                )
            ],
            gaps=[],
        )
    )
    graph.get_entity_neighbourhood = AsyncMock(
        return_value=SearchResult(nodes=[], edges=[])
    )
    graph.list_entities = AsyncMock(
        return_value=[
            GraphNode(text="ВТ6", type="MATERIAL", sources=["paper-001"]),
        ]
    )
    return graph


@pytest_asyncio.fixture
async def client(mock_graph):
    """nlp is no longer in app.state — routes call get_nlp_for_text() directly."""
    app = FastAPI()
    app.include_router(router)
    app.state.graph = mock_graph

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_ingest_document(client, mock_graph):
    mock_graph.upsert_entities.reset_mock()
    mock_graph.upsert_relations.reset_mock()

    payload = {
        "doc_id": "t-001",
        "text": "Сплав ВТ6 при 850°C повысил твёрдость.",
        "meta": {},
    }
    resp = await client.post("/api/v1/documents", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["doc_id"] == "t-001"
    assert isinstance(body["entities"], list)
    assert isinstance(body["relations"], list)
    mock_graph.upsert_entities.assert_called_once()
    mock_graph.upsert_relations.assert_called_once()
    mock_graph.upsert_document.assert_called_once_with(
        "t-001", payload["text"], payload["meta"]
    )


@pytest.mark.asyncio
async def test_get_document(client, mock_graph):
    mock_graph.get_document = AsyncMock(
        return_value={
            "doc_id": "paper-001",
            "text": "Отжиг сплава ВТ6 повысил прочность.",
            "meta": {"title": "Отжиг ВТ6", "year": 2022},
        }
    )
    resp = await client.get("/api/v1/documents/paper-001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["doc_id"] == "paper-001"
    assert body["meta"]["title"] == "Отжиг ВТ6"


@pytest.mark.asyncio
async def test_get_document_not_found(client, mock_graph):
    mock_graph.get_document = AsyncMock(return_value=None)
    resp = await client.get("/api/v1/documents/unknown-doc")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_search_returns_result(client):
    resp = await client.get("/api/v1/search", params={"material": "ВТ6"})
    assert resp.status_code == 200
    body = resp.json()
    assert "nodes" in body
    assert "edges" in body
    assert "gaps" in body


@pytest.mark.asyncio
async def test_search_requires_filter(client):
    resp = await client.get("/api/v1/search")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_entities(client):
    resp = await client.get("/api/v1/entities")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_batch_ingest(client, mock_graph):
    payload = [
        {"doc_id": "t-001", "text": "ВТ6 при 850°C повысил прочность.", "meta": {}},
        {"doc_id": "t-002", "text": "Ti-6Al-4V после закалки при 950°C.", "meta": {}},
    ]
    resp = await client.post("/api/v1/documents/batch", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert len(body) == 2
    assert body[0]["doc_id"] == "t-001"
    assert body[1]["doc_id"] == "t-002"
    assert mock_graph.upsert_document.call_count == 2
