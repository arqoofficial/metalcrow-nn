"""Поиск по `experiments.experiments_flat` (SPEC_V3 §5.2/§8.2).

TODO(SPEC_V3 §5.2): реальный 4-ступенчатый pipeline (SQL pre-filter -> BM25 + vector +
custom distance metric -> Reciprocal Rank Fusion -> опц. LLM rerank). В этой фазе —
единственный ILIKE-проход по `experiments_flat`; `search_meta.vector_hits`/`custom_hits`
всегда 0, `reranked` всегда False, `score` — заглушка (1.0).
"""

from typing import Any

from sqlalchemy import text
from sqlmodel import Session

from app.schemas.search import (
    SearchFilters,
    SearchMeta,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SearchResultRegime,
    SearchResultSource,
)

_SELECT_COLUMNS = (
    "id, title, material_name, material_composition, temperature, pressure, "
    "duration, medium, property_name, property_value, property_unit, "
    "source_doc, source_page, source_paragraph"
)


def _build_where(request: SearchRequest) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if request.query:
        clauses.append(
            "(title ILIKE :q OR material_name ILIKE :q OR property_name ILIKE :q "
            "OR conclusion ILIKE :q)"
        )
        params["q"] = f"%{request.query}%"

    filters = request.filters or SearchFilters()
    if filters.material:
        clauses.append("material_name ILIKE :material")
        params["material"] = f"%{filters.material}%"
    if filters.material_type:
        clauses.append("material_type = :material_type")
        # raw SQL bypasses SQLAlchemy Enum-type adaptation, которая по умолчанию
        # хранит .name ('ALLOY'), а не .value ('alloy') — сверяемся с этим напрямую.
        params["material_type"] = filters.material_type.name
    if filters.temperature_min is not None:
        clauses.append("temperature >= :temperature_min")
        params["temperature_min"] = filters.temperature_min
    if filters.temperature_max is not None:
        clauses.append("temperature <= :temperature_max")
        params["temperature_max"] = filters.temperature_max
    if filters.tags:
        clauses.append("tags && :tags")
        params["tags"] = filters.tags
    if filters.lab:
        clauses.append("lab_name ILIKE :lab")
        params["lab"] = f"%{filters.lab}%"
    # NB: experiments_flat не содержит e.date отдельно от created_at (TODO: добавить
    # в materialized view, если потребуется точная фильтрация по дате проведения).
    if filters.date_from:
        clauses.append("created_at::date >= :date_from")
        params["date_from"] = filters.date_from
    if filters.date_to:
        clauses.append("created_at::date <= :date_to")
        params["date_to"] = filters.date_to

    where_sql = " AND ".join(clauses) if clauses else "TRUE"
    return where_sql, params


def hybrid_search(session: Session, request: SearchRequest) -> SearchResponse:
    where_sql, params = _build_where(request)

    total = session.execute(
        text(f"SELECT count(*) FROM experiments.experiments_flat WHERE {where_sql}"),
        params,
    ).scalar_one()

    rows = (
        session.execute(
            text(
                f"SELECT {_SELECT_COLUMNS} FROM experiments.experiments_flat "
                f"WHERE {where_sql} ORDER BY created_at DESC NULLS LAST LIMIT :top_k"
            ),
            {**params, "top_k": request.top_k},
        )
        .mappings()
        .all()
    )

    results = [
        SearchResultItem(
            experiment_id=row["id"],
            title=row["title"],
            material=row["material_name"],
            material_composition=row["material_composition"],
            regime=SearchResultRegime(
                temperature=row["temperature"],
                pressure=row["pressure"],
                duration=row["duration"],
                medium=row["medium"],
            ),
            property=row["property_name"],
            value=row["property_value"],
            unit=row["property_unit"],
            score=1.0,
            source=SearchResultSource(
                document=row["source_doc"],
                page=row["source_page"],
                paragraph=row["source_paragraph"],
            ),
        )
        for row in rows
    ]

    return SearchResponse(
        results=results,
        total=total,
        search_meta=SearchMeta(
            bm25_hits=len(results), vector_hits=0, custom_hits=0, reranked=False
        ),
    )
