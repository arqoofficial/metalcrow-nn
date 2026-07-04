from typing import Any

from sqlmodel import Field, SQLModel


# POST /api/v1/graph/query request body (SPEC_V3 §8.3, Приложение D.5).
# Только шаблон + параметры — raw Cypher от клиента не принимается.
class GraphQueryRequest(SQLModel):
    template_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    max_depth: int = Field(default=3, ge=1, le=5)


# Точная форма node/edge не зафиксирована в Приложении D (P1/P2 стретч) — заведена по
# ontology-диаграмме §4 (label/type + произвольные properties из БД/графа).
class GraphNode(SQLModel):
    id: str
    label: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(SQLModel):
    source: str
    target: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)


# Response for POST /graph/query и GET /graph/subgraph/{entity_id}
class SubgraphResponse(SQLModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


# Response for GET /api/v1/graph/path (P2, SPEC_V3 §8.3) — Neo4j down -> 503, без SQL fallback
class PathResponse(SQLModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    path_length: int
