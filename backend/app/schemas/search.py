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
