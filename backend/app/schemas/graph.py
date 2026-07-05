from typing import Any

from sqlmodel import Field, SQLModel

from app.schemas.common import RegimeBucket


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


# Пробел покрытия для GET /api/v1/graph/overview: комбинация material × property ×
# regime_bucket, по которой нет ни одного эксперимента (переиспользует heatmap-логику
# analytics.coverage). `reason` — человекочитаемая формулировка для UI.
class GraphGap(SQLModel):
    material: str
    property: str
    regime_bucket: RegimeBucket
    reason: str


# Response for GET /api/v1/graph/overview — агрегированный граф знаний по experiments_flat
# (цепочки material→process→equipment→result + labs/experts) плюс пробелы покрытия.
class GraphOverviewResponse(SQLModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    gaps: list[GraphGap] = Field(default_factory=list)
    # Свободнотекстовые пробелы (источник GraphRAG отдаёт их строками, а не
    # структурированными material×property×regime как SQL-coverage).
    notes: list[str] = Field(default_factory=list)
