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
from app.schemas.common import RegimeBucket
from app.schemas.graph import (
    GraphEdge,
    GraphGap,
    GraphNode,
    GraphOverviewResponse,
    SubgraphResponse,
)
from app.services import analytics as analytics_service
from app.services import science_kg_client

logger = logging.getLogger(__name__)

_NEIGHBOUR_COLUMNS: list[tuple[str, str, str]] = [
    ("material_name", "Material", "HAS_MATERIAL"),
    ("property_name", "Property", "HAS_RESULT"),
    ("lab_name", "Lab", "PERFORMED_AT"),
    ("researcher", "Researcher", "BY"),
    ("equipment_name", "Equipment", "ON_EQUIPMENT"),
]

# Максимум пробелов (material × property × regime без экспериментов) в ответе /overview,
# чтобы не раздувать граф синтетическими Gap-узлами на разреженном покрытии.
_MAX_GAPS = 50

# Типы сущностей science-knowledge-graph (GraphRAG) -> канонические типы узлов UI.
_KG_TYPE_MAP: dict[str, str] = {
    "MATERIAL": "Material",
    "PROCESS": "Process",
    "EQUIPMENT": "Equipment",
    "PROPERTY": "Result",
    "FACILITY": "Lab",
    "EXPERT": "Expert",
    "PUBLICATION": "Publication",
    "EXPERIMENT": "Experiment",
}


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


def _overview_gaps(
    session: Session,
    materials_in_graph: set[str],
    properties_in_graph: set[str],
    regime: RegimeBucket | None,
) -> list[GraphGap]:
    """Комбинации material × property × regime без экспериментов (пробелы, §5.4).

    Переиспользует heatmap-грид `analytics._coverage_grid` (та же логика, что у
    `/analytics/coverage`), ограничивает пробелы материалами/свойствами, уже
    присутствующими в текущем графе, и капит их числом (`_MAX_GAPS`).
    """
    if not materials_in_graph or not properties_in_graph:
        return []
    _materials, _properties, counts = analytics_service._coverage_grid(session)
    buckets = [regime] if regime is not None else list(RegimeBucket)
    gaps: list[GraphGap] = []
    for mat in sorted(materials_in_graph):
        for prop in sorted(properties_in_graph):
            for bucket in buckets:
                if counts.get((mat, prop, bucket.value), 0) != 0:
                    continue
                gaps.append(
                    GraphGap(
                        material=mat,
                        property=prop,
                        regime_bucket=bucket,
                        reason=(
                            f"нет экспериментов для комбинации: {mat} + "
                            f"{bucket.value} режим + {prop}"
                        ),
                    )
                )
                if len(gaps) >= _MAX_GAPS:
                    return gaps
    return gaps


def overview(
    session: Session,
    *,
    material: str | None = None,
    property_: str | None = None,
    regime: RegimeBucket | None = None,
    limit: int = 300,
) -> GraphOverviewResponse:
    """Агрегированный граф знаний по `experiments_flat` (§5.3/§8.3, SQL fallback).

    Дедуплицирует material/process/equipment/result/lab/expert в узлы (вес = число
    экспериментов), строит цепочку material→process→equipment→result и привязывает
    лаборатории/экспертов (material→lab→expert), плюс подмешивает пробелы покрытия
    (комбинации без экспериментов) синтетическими `Gap`-узлами. Neo4j не требуется.
    """
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if material:
        clauses.append("material_name ILIKE :material")
        params["material"] = f"%{material}%"
    if property_:
        clauses.append("property_name ILIKE :property")
        params["property"] = f"%{property_}%"
    if regime is not None:
        clauses.append(f"({analytics_service._REGIME_BUCKET_CASE.strip()}) = :regime")
        params["regime"] = regime.value
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    rows = (
        session.execute(
            text(
                "SELECT material_name, medium, equipment_name, property_name, "
                "lab_name, researcher "
                f"FROM experiments.experiments_flat {where} "
                "LIMIT :limit"
            ),
            params,
        )
        .mappings()
        .all()
    )

    nodes: dict[str, GraphNode] = {}
    edges: dict[tuple[str, str, str], GraphEdge] = {}

    def _node(node_type: str, value: str) -> str:
        node_id = f"{node_type.lower()}:{value}"
        node = nodes.get(node_id)
        if node is None:
            nodes[node_id] = GraphNode(
                id=node_id, label=value, type=node_type, properties={"count": 1}
            )
        else:
            node.properties["count"] += 1
        return node_id

    def _edge(source: str, target: str, edge_type: str) -> None:
        key = (source, target, edge_type)
        edge = edges.get(key)
        if edge is None:
            edges[key] = GraphEdge(
                source=source, target=target, type=edge_type, properties={"count": 1}
            )
        else:
            edge.properties["count"] += 1

    materials_in_graph: set[str] = set()
    properties_in_graph: set[str] = set()

    for row in rows:
        mat_id = _node("Material", row["material_name"]) if row["material_name"] else None
        proc_id = _node("Process", row["medium"]) if row["medium"] else None
        equip_id = _node("Equipment", row["equipment_name"]) if row["equipment_name"] else None
        prop_id = _node("Result", row["property_name"]) if row["property_name"] else None
        lab_id = _node("Lab", row["lab_name"]) if row["lab_name"] else None
        expert_id = _node("Expert", row["researcher"]) if row["researcher"] else None

        if row["material_name"]:
            materials_in_graph.add(row["material_name"])
        if row["property_name"]:
            properties_in_graph.add(row["property_name"])

        # цепочка material → process → equipment → result (с «перемычками» при пропусках,
        # чтобы узлы не оставались висячими, если часть звена отсутствует)
        if mat_id and proc_id:
            _edge(mat_id, proc_id, "HAS_PROCESS")
        if proc_id and equip_id:
            _edge(proc_id, equip_id, "ON_EQUIPMENT")
        elif mat_id and not proc_id and equip_id:
            _edge(mat_id, equip_id, "ON_EQUIPMENT")
        if equip_id and prop_id:
            _edge(equip_id, prop_id, "PRODUCES")
        elif prop_id and (proc_id or mat_id) and not equip_id:
            _edge(proc_id or mat_id, prop_id, "PRODUCES")  # type: ignore[arg-type]

        # эксперты и лаборатории по теме (material)
        if mat_id and lab_id:
            _edge(mat_id, lab_id, "STUDIED_AT")
        if lab_id and expert_id:
            _edge(lab_id, expert_id, "HAS_EXPERT")
        elif mat_id and expert_id:
            _edge(mat_id, expert_id, "HAS_EXPERT")

    gaps = _overview_gaps(session, materials_in_graph, properties_in_graph, regime)
    for gap in gaps:
        gap_id = f"gap:{gap.material}|{gap.regime_bucket.value}|{gap.property}"
        nodes[gap_id] = GraphNode(
            id=gap_id, label="⚠ пробел", type="Gap", properties={"reason": gap.reason}
        )
        for target in (f"material:{gap.material}", f"result:{gap.property}"):
            if target in nodes:
                edges[(gap_id, target, "GAP")] = GraphEdge(
                    source=gap_id, target=target, type="GAP", properties={"gap": True}
                )

    return GraphOverviewResponse(
        nodes=list(nodes.values()), edges=list(edges.values()), gaps=gaps
    )


def kg_overview(
    *, q: str | None = None, depth: int = 2, limit: int = 300
) -> GraphOverviewResponse:
    """Агрегированный граф из science-knowledge-graph (GraphRAG) — реальные сущности,
    извлечённые из документов, а не SQL-таблица экспериментов.

    `q` задан -> окрестность точной сущности (`neighbourhood`), при промахе fallback на
    подстрочный `search(material=q)`. Без `q` -> окрестность первого материала из
    каталога. Недоступность сервиса -> пустой результат (как и остальной graph-сервис).
    """
    result: dict[str, Any] | None = None
    if q:
        result = science_kg_client.neighbourhood(q, depth=depth)
        if not result or not result.get("nodes"):
            result = science_kg_client.search(material=q, limit=limit)
    else:
        catalogue = science_kg_client.entities(type_="MATERIAL", limit=1)
        seed = catalogue[0]["text"] if catalogue else None
        if seed:
            result = science_kg_client.neighbourhood(seed, depth=depth)

    if not result:
        return GraphOverviewResponse(nodes=[], edges=[], gaps=[], notes=[])

    nodes: list[GraphNode] = []
    node_ids: set[str] = set()
    for n in result.get("nodes", [])[:limit]:
        text = n.get("text")
        if not text or text in node_ids:
            continue
        node_ids.add(text)
        props: dict[str, Any] = {"kg_type": n.get("type")}
        sources = n.get("sources") or []
        if sources:
            props["count"] = len(sources)
            props["sources"] = sources[:5]
        if n.get("unit"):
            props["unit"] = n["unit"]
        if n.get("value_nominal") is not None:
            props["value"] = n["value_nominal"]
        nodes.append(
            GraphNode(
                id=text,
                label=text,
                type=_KG_TYPE_MAP.get(n.get("type", ""), "Other"),
                properties=props,
            )
        )

    edges: list[GraphEdge] = []
    for e in result.get("edges", []):
        source, target = e.get("source"), e.get("target")
        if source in node_ids and target in node_ids:
            edges.append(
                GraphEdge(
                    source=source,
                    target=target,
                    type=e.get("relation") or "RELATED",
                    properties={"verb": e.get("verb", "")},
                )
            )

    notes = [str(g) for g in result.get("gaps", []) if g]
    return GraphOverviewResponse(nodes=nodes, edges=edges, gaps=[], notes=notes)


def path(
    from_id: str, to_id: str, max_depth: int
) -> tuple[list[GraphNode], list[GraphEdge]]:
    logger.info("graph path stub: %s -> %s max_depth=%s", from_id, to_id, max_depth)
    return [], []
