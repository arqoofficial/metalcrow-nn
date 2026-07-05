"""Поиск по `experiments.experiments_flat` (SPEC_V3 §5.2/§8.2).

TODO(SPEC_V3 §5.2): реальный 4-ступенчатый pipeline (SQL pre-filter -> BM25 + vector +
custom distance metric -> Reciprocal Rank Fusion -> опц. LLM rerank). В этой фазе —
единственный ILIKE-проход по `experiments_flat`; `search_meta.vector_hits`/`custom_hits`
всегда 0, `reranked` всегда False, `score` — заглушка (1.0).
"""

import logging
import re
from typing import Any

from sqlalchemy import text
from sqlmodel import Session

from app.schemas.search import (
    CorpusDocumentHit,
    CorpusPassageHit,
    CorpusSearchRequest,
    CorpusSearchResponse,
    RecognizedEntities,
    SearchFilters,
    SearchMeta,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SearchResultRegime,
    SearchResultSource,
)
from app.services import ontology_client
from app.services import wiki as wiki_service

logger = logging.getLogger(__name__)

_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)

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


# ── Поиск по корпусу: онтология (пассажи) + парсер (документы) ──────────────


def _passage_passes(p: dict[str, Any], request: CorpusSearchRequest) -> bool:
    """Пост-фильтры типа, года и географии. Пассаж с неизвестными метаданными
    при активном фильтре НЕ отбрасывается — иначе фильтр скрывал бы большую
    часть корпуса (год/язык заполнены не у всех документов)."""
    if request.kinds and p.get("kind") not in request.kinds:
        return False
    year = p.get("year")
    if year is not None:
        if request.year_from is not None and year < request.year_from:
            return False
        if request.year_to is not None and year > request.year_to:
            return False
    if request.geo in ("ru", "en"):
        lang, country = p.get("lang"), p.get("country")
        if lang is not None or country is not None:
            is_ru = lang == "ru" or country == "RU"
            if (request.geo == "ru") != is_ru:
                return False
    return True


# отображаемая единица UI → каноническая единица сервиса онтологии:
# только temperature требует конверсии (°C на фронте, K в БД по контракту).
def _numeric_to_canonical(quantity: str, v: float | None) -> float | None:
    if v is None:
        return None
    if quantity == "temperature":
        return v + 273.15
    return v


def corpus_search(request: CorpusSearchRequest) -> CorpusSearchResponse:
    """Поиск по корпусу для вкладки «Поиск».

    Два источника: (1) онтология `search_passages` — выводы и измерения с
    дословным сниппетом, документом-источником и распознанными сущностями
    запроса; (2) парсер — обработанные markdown-документы по имени/пути.
    Любой из источников недоступен → деградация до второго, не ошибка."""
    numeric = request.numeric
    numeric_active = numeric is not None and (
        numeric.value_from is not None or numeric.value_to is not None
    )
    if not request.query.strip() and not numeric_active:
        return CorpusSearchResponse(query=request.query)

    # при активных пост-фильтрах (тип/год/гео) выбираем у сервиса с запасом:
    # он режет топ-N по рангу ДО фильтрации, и без запаса отфильтрованная
    # выдача была бы недозаполненной или ложно пустой.
    has_filters = (
        bool(request.kinds)
        or request.geo in ("ru", "en")
        or (request.year_from is not None or request.year_to is not None)
    )
    fetch_limit = min(request.limit * 3, 60) if has_filters else request.limit
    if numeric_active and numeric is not None:
        # числовое условие ведёт поиск: измерения величины в диапазоне,
        # текст запроса лишь сужает выдачу на стороне сервиса онтологии.
        raw = (
            ontology_client.invoke(
                "search_measurements",
                {
                    "quantity": numeric.quantity,
                    "value_from": _numeric_to_canonical(
                        numeric.quantity, numeric.value_from
                    ),
                    "value_to": _numeric_to_canonical(
                        numeric.quantity, numeric.value_to
                    ),
                    "query": request.query,
                    "limit": fetch_limit,
                },
            )
            or {}
        )
    else:
        raw = (
            ontology_client.invoke(
                "search_passages", {"query": request.query, "limit": fetch_limit}
            )
            or {}
        )

    passages = [
        CorpusPassageHit(
            kind=p.get("kind") or "finding",
            doc=p.get("doc") or "источник не указан",
            text=p.get("text"),
            snippet=p.get("snippet"),
            okf_path=p.get("okf_path"),
            locator=p.get("locator"),
            year=p.get("year"),
            country=p.get("country"),
            lang=p.get("lang"),
            value=p.get("value"),
            unit=p.get("unit"),
            rank=float(p.get("rank") or 0.0),
        )
        for p in (raw.get("passages") or [])
        if _passage_passes(p, request)
    ][: request.limit]

    documents: list[CorpusDocumentHit] = []
    if (
        request.include_documents
        and request.query.strip()
        and (not request.kinds or "document" in request.kinds)
    ):
        # parser_client оборачивает в ParserError только HTTP-статусы; сетевые
        # ошибки (парсер не поднят) летят сырыми httpx-исключениями — документы
        # вторичный источник, любой их сбой не должен ронять поиск целиком.
        try:
            wiki_response = wiki_service.search_documents(request.query, limit=10)
            documents = [
                CorpusDocumentHit(okf_path=r.okf_path, title=r.title, snippet=r.snippet)
                for r in wiki_response.results
            ]
        except Exception as exc:  # noqa: BLE001 — деградация, не сбой поиска
            logger.warning("corpus_search: документы недоступны: %s", exc)
            documents = []
    if request.geo in ("ru", "en") and documents:
        # у markdown-документов нет метаданных языка — эвристика по кириллице
        # в имени файла (заголовок = имя исходника)
        documents = [
            d
            for d in documents
            if bool(_CYRILLIC_RE.search(d.title)) == (request.geo == "ru")
        ]

    entities_raw = raw.get("entities") or {}
    return CorpusSearchResponse(
        query=request.query,
        passages=passages,
        documents=documents,
        entities=RecognizedEntities(
            process=entities_raw.get("process"),
            quantity_kind=entities_raw.get("quantity_kind"),
            materials=entities_raw.get("materials") or [],
        ),
        expanded_terms=raw.get("expanded_terms") or [],
        note=raw.get("note"),
        total_passages=len(passages),
        total_documents=len(documents),
    )
