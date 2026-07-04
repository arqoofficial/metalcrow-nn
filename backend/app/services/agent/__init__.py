"""Agent tools, Приложение C. Реализация — прямые вызовы `app.services.*` функций
внутри одного процесса (не HTTP), без реального LLM-агента (TODO SPEC_V3 §5.7 P1/P2:
tool-calling loop, structured claims validator, degraded-mode retry).
"""

import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlmodel import Session

from app.schemas.chat import Claim, ClaimConfidence, ClaimGapCell, ClaimKind, GapCell
from app.schemas.graph import SubgraphResponse
from app.schemas.search import SearchRequest, SearchResponse
from app.services import graph as graph_service
from app.services import science_kg_client
from app.services import search as search_service

logger = logging.getLogger(__name__)


# P0: hybrid_search {query, filters?, top_k?, rerank?} -> {results, search_meta}
def hybrid_search(session: Session, request: SearchRequest) -> SearchResponse:
    return search_service.hybrid_search(session, request)


# P0: sql_filter {template_id, params} -> {rows: Experiment[]}
# TODO(Приложение C): библиотека именованных SQL-шаблонов фильтрации (потребуют
# `_session`); заглушка — []
def sql_filter(
    _session: Session, template_id: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    logger.info("sql_filter stub: template_id=%s params=%s", template_id, params)
    return []


# P0: sql_aggregate {template_id, params} -> {aggregation: {groups[], totals}}
# TODO(Приложение C): библиотека шаблонов агрегации (потребуют `_session`); заглушка —
# нулевая агрегация
def sql_aggregate(
    _session: Session, template_id: str, params: dict[str, Any]
) -> dict[str, Any]:
    logger.info("sql_aggregate stub: template_id=%s params=%s", template_id, params)
    return {"groups": [], "totals": {}}


# P0: get_experiment_details {experiment_id} -> {experiment: ExperimentFull}
def get_experiment_details(
    session: Session, experiment_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        session.execute(
            text("SELECT * FROM experiments.experiments_flat WHERE id = :id"),
            {"id": str(experiment_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


# P1: generate_hypothesis {gap_cell} -> {claim: Claim(kind=hypothesis)}
# Пробует science-knowledge-graph (spaCy NER + Neo4j GraphRAG, отдельный
# internal-only сервис — services/science-knowledge-graph/README.md) через
# `/rag/query`: вопрос собирается из gap_cell, ответ — уже LLM-текст с
# provenance по графу. Недоступность science-knowledge-graph или пустой ответ
# -> тот же degraded-mode stub-текст, что и раньше (§8.4: без чисел, confidence=low).
# NB: claims validator (>=1 experiment_id для hypothesis) в этой фазе не
# применяется, а science-knowledge-graph вообще не оперирует experiment_id
# (свои, NER-извлечённые сущности) — experiment_ids остаётся [] в обоих ветках.
def generate_hypothesis(gap_cell: GapCell) -> Claim:
    gap_cell_schema = ClaimGapCell(
        material=gap_cell.material,
        property=gap_cell.property,
        regime_bucket=gap_cell.regime_bucket.value,
    )

    question = (
        f"Какие эксперименты и эффекты известны для материала «{gap_cell.material}», "
        f"свойства «{gap_cell.property}» в режиме '{gap_cell.regime_bucket.value}'?"
    )
    # generate_answer() (science-knowledge-graph) always calls the LLM and decides
    # itself whether the graph has enough to answer — gap_cell questions are always
    # domain questions (never casual chat), so a real `answer` here already means
    # the LLM gave a genuine (possibly "insufficient data for X") response.
    result = science_kg_client.rag_query(question)
    if result and result.get("answer"):
        sources = result.get("sources") or []
        return Claim(
            text=result["answer"],
            experiment_ids=[],
            confidence=ClaimConfidence.MEDIUM if sources else ClaimConfidence.LOW,
            kind=ClaimKind.HYPOTHESIS,
            gap_cell=gap_cell_schema,
        )

    return Claim(
        text=(
            f"Недостаточно данных по материалу «{gap_cell.material}», "
            f"свойству «{gap_cell.property}» в режиме '{gap_cell.regime_bucket.value}' "
            "— такой эксперимент ещё не проводился."
        ),
        experiment_ids=[],
        confidence=ClaimConfidence.LOW,
        kind=ClaimKind.HYPOTHESIS,
        gap_cell=gap_cell_schema,
    )


# P1: graph_template {template_id, params, max_depth?} -> {nodes[], edges[]}
def graph_template(
    template_id: str, params: dict[str, Any], max_depth: int = 3
) -> SubgraphResponse:
    return graph_service.run_template(template_id, params, max_depth)


# P2: get_subgraph {entity_ids, depth, max_nodes} -> {nodes[], edges[]}
# TODO(Приложение C, P2): агрегация мини-графа сразу по нескольким сущностям с
# обрезкой по max_nodes; заглушка использует SQL fallback только для первой сущности
def get_subgraph(
    session: Session, entity_ids: list[uuid.UUID], depth: int = 1, max_nodes: int = 12
) -> SubgraphResponse:
    if not entity_ids:
        return SubgraphResponse(nodes=[], edges=[])
    subgraph = graph_service.sql_subgraph(session, str(entity_ids[0]), depth)
    return SubgraphResponse(
        nodes=subgraph.nodes[:max_nodes], edges=subgraph.edges[:max_nodes]
    )
