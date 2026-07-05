import uuid
from datetime import date as date_
from enum import StrEnum
from typing import Any

from sqlmodel import Field, SQLModel

from app.models.materials import MaterialType


class SearchMode(StrEnum):
    BM25 = "bm25"
    VECTOR = "vector"
    HYBRID = "hybrid"
    CUSTOM = "custom"


class SearchFilters(SQLModel):
    material: str | None = None
    material_type: MaterialType | None = None
    temperature_min: float | None = None
    temperature_max: float | None = None
    tags: list[str] | None = None
    lab: str | None = None
    date_from: date_ | None = None
    date_to: date_ | None = None


# POST /api/v1/search request body (SPEC_V3 §8.2, Приложение D.1). `query` не имеет
# minLength в спецификации — пустая строка допустима (поиск только по фильтрам).
class SearchRequest(SQLModel):
    query: str
    filters: SearchFilters | None = None
    search_mode: SearchMode = SearchMode.HYBRID
    top_k: int = Field(default=20, ge=1, le=100)
    rerank: bool = False


class SearchResultRegime(SQLModel):
    temperature: float | None = None
    pressure: float | None = None
    duration: float | None = None
    medium: str | None = None


class SearchResultSource(SQLModel):
    document_id: uuid.UUID | None = None
    document: str | None = None
    page: int | None = None
    paragraph: str | None = None


class SearchResultItem(SQLModel):
    experiment_id: uuid.UUID
    title: str | None = None
    material: str | None = None
    material_composition: dict[str, Any] | None = None
    regime: SearchResultRegime | None = None
    property: str | None = None
    value: float | None = None
    unit: str | None = None
    score: float
    source: SearchResultSource


# TODO(SPEC_V3 §5.2): реальный BM25+vector+custom / RRF; в этой фазе — ILIKE/FTS-заглушка
class SearchMeta(SQLModel):
    bm25_hits: int = 0
    vector_hits: int = 0
    custom_hits: int = 0
    reranked: bool = False


# Response for POST /api/v1/search (SPEC_V3 §8.2, Приложение D.2)
class SearchResponse(SQLModel):
    results: list[SearchResultItem]
    total: int
    search_meta: SearchMeta


# ── Поиск по корпусу: пассажи онтологии + markdown-документы ────────────────
# Вкладка «Поиск»: онтологический ретрив (search_passages: выводы/измерения с
# дословным сниппетом и документом-источником) + подбор обработанных документов.


class RecognizedEntities(SQLModel):
    """Сущности, распознанные онтологией в тексте запроса (канонические имена)."""

    process: str | None = None
    quantity_kind: str | None = None
    materials: list[str] = []


class NumericCondition(SQLModel):
    """Числовое условие: величина + диапазон значений в её отображаемой
    единице (temperature — °C, concentration — г/л, доли — %)."""

    quantity: str = Field(min_length=1, max_length=64)
    value_from: float | None = None
    value_to: float | None = None


class CorpusSearchRequest(SQLModel):
    # пустой запрос допустим при активном числовом условии (поиск по условию)
    query: str = Field(default="", max_length=500)
    limit: int = Field(default=20, ge=1, le=50)
    # фильтр типов результата: measurement | finding | recommendation | document
    kinds: list[str] | None = None
    year_from: int | None = None
    year_to: int | None = None
    # география/язык источника: 'ru' — отечественные, 'en' — мировые
    geo: str | None = None
    numeric: NumericCondition | None = None
    include_documents: bool = True


class CorpusPassageHit(SQLModel):
    kind: str
    doc: str
    text: str | None = None
    snippet: str | None = None
    okf_path: str | None = None
    locator: str | None = None
    year: int | None = None
    country: str | None = None
    lang: str | None = None
    value: str | None = None
    unit: str | None = None
    rank: float = 0.0


class CorpusDocumentHit(SQLModel):
    okf_path: str
    title: str
    snippet: str | None = None


class CorpusSearchResponse(SQLModel):
    query: str
    passages: list[CorpusPassageHit] = []
    documents: list[CorpusDocumentHit] = []
    entities: RecognizedEntities = Field(default_factory=RecognizedEntities)
    # термы, добавленные к запросу словарём синонимов/аббревиатур (кросс-язык)
    expanded_terms: list[str] = []
    note: str | None = None
    total_passages: int = 0
    total_documents: int = 0
