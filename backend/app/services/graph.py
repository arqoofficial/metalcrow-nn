"""Граф-сервис (SPEC_V3 §5.3/§8.3).

`run_template` делегирует в `science-knowledge-graph` (отдельный internal-only
сервис — spaCy NER + свой Neo4j-граф, services/science-knowledge-graph/README.md)
через `/api/v1/search`; это не библиотека именованных Cypher-шаблонов
(TODO SPEC_V3 §5.3), а прямой проброс `params` в фильтр material/regime/property.
Недоступность science-knowledge-graph -> пустой результат, не ошибка (та же
деградация, что и для Neo4j ниже).

TODO(SPEC_V3 §5.3): настоящий Neo4j `shortestPath` для `/graph/path` — `path`
остаётся заглушкой, science-knowledge-graph не отдаёт point-to-point path, только
search/neighbourhood. `sql_subgraph` — реальный SQL fallback вокруг эксперимента
(без Neo4j), как того требует §8.3 для `/subgraph`.
"""

import logging
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from sqlalchemy import text
from sqlmodel import Session

from app.core.config import settings
from app.schemas.graph import GraphEdge, GraphNode, SubgraphResponse
from app.services import science_kg_client

logger = logging.getLogger(__name__)

_NEIGHBOUR_COLUMNS: list[tuple[str, str, str]] = [
    ("material_name", "Material", "HAS_MATERIAL"),
    ("property_name", "Property", "HAS_RESULT"),
    ("lab_name", "Lab", "PERFORMED_AT"),
    ("researcher", "Researcher", "BY"),
    ("equipment_name", "Equipment", "ON_EQUIPMENT"),
]


def neo4j_available() -> bool:
    """Короткий ping; False -> роутер отдаёт 503 для `/graph/path` (без SQL fallback, §8.3)."""
    try:
        driver = GraphDatabase.driver(
            settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        )
        try:
            driver.verify_connectivity()
            return True
        finally:
            driver.close()
    except (ServiceUnavailable, Neo4jError, OSError):
        return False


def run_template(
    template_id: str, params: dict[str, Any], _max_depth: int
) -> SubgraphResponse:
    result = science_kg_client.search(
        material=params.get("material"),
        regime=params.get("regime"),
        property_=params.get("property"),
        limit=params.get("limit", 20),
    )
    if result is None:
        logger.warning("science-knowledge-graph unavailable for template_id=%s", template_id)
        return SubgraphResponse(nodes=[], edges=[])

    nodes = [
        GraphNode(id=n["text"], label=n["text"], type=n["type"])
        for n in result.get("nodes", [])
    ]
    edges = [
        GraphEdge(source=e["source"], target=e["target"], type=e["relation"])
        for e in result.get("edges", [])
    ]
    return SubgraphResponse(nodes=nodes, edges=edges)


def sql_subgraph(session: Session, entity_id: str, _depth: int) -> SubgraphResponse:
    """Подграф вокруг эксперимента: прямые связи из `experiments_flat` (глубина 1).

    TODO(§5.3): рекурсивный обход при `_depth` > 1 — сейчас параметр не используется.
    """
    row = (
        session.execute(
            text(
                "SELECT id, title, material_name, property_name, lab_name, "
                "researcher, equipment_name FROM experiments.experiments_flat "
                "WHERE id = :id"
            ),
            {"id": entity_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return SubgraphResponse(nodes=[], edges=[])

    center = GraphNode(
        id=str(row["id"]), label=row["title"] or str(row["id"]), type="Experiment"
    )
    nodes = [center]
    edges: list[GraphEdge] = []

    for column, node_type, edge_type in _NEIGHBOUR_COLUMNS:
        value = row[column]
        if not value:
            continue
        node_id = f"{node_type.lower()}:{value}"
        nodes.append(GraphNode(id=node_id, label=value, type=node_type))
        edges.append(GraphEdge(source=center.id, target=node_id, type=edge_type))

    return SubgraphResponse(nodes=nodes, edges=edges)


def path(
    from_id: str, to_id: str, max_depth: int
) -> tuple[list[GraphNode], list[GraphEdge]]:
    logger.info("graph path stub: %s -> %s max_depth=%s", from_id, to_id, max_depth)
    return [], []
