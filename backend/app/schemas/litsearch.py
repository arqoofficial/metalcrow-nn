import uuid
from typing import Literal

from sqlmodel import Field, SQLModel

from app.models.litsearch import FetchStatus, FulltextStatus, LitIngestStatus, LitStage

__all__ = [
    "LiteratureRef",
    "LiteraturePaperPublic",
    "LitAnswerRef",
    "LiteratureSearchPublic",
    "PaperIngestStatusPublic",
]


# Компактная ссылка на literature search, встраивается в ChatMessageResponse.literature
# (litsearch -> chat integration, companion spec §4.5), mirrors SubgraphResponse opt-in slot.
class LiteratureRef(SQLModel):
    search_id: uuid.UUID
    paper_count: int


# Публичное представление LiteraturePaper (app.models.litsearch), companion spec §4.5.
class LiteraturePaperPublic(SQLModel):
    id: uuid.UUID
    doi: str | None = None
    title: str
    authors: str
    year: int | None = None
    abstract: str
    pdf_url: str | None = None
    citation_count: int | None = None
    fetch_status: FetchStatus
    fulltext_status: FulltextStatus
    fulltext_chars: int
    ingest_status: LitIngestStatus
    document_id: uuid.UUID | None = None


# Ссылка на chat-сообщение, отвечавшее на основе abstracts/fulltext литературы
# (companion spec §4.5).
class LitAnswerRef(SQLModel):
    message_id: uuid.UUID
    kind: Literal["abstracts", "fulltext"]


# Публичное представление LiteratureSearch (app.models.litsearch), companion spec §4.5.
class LiteratureSearchPublic(SQLModel):
    id: uuid.UUID
    stage: LitStage
    round: int
    followup_search_id: uuid.UUID | None = None
    papers: list[LiteraturePaperPublic] = Field(default_factory=list)
    answers: list[LitAnswerRef] = Field(default_factory=list)
    # Every search's question in this chat turn (anchor first), Task 4: the
    # panel used to only ever see the anchor's own question — this surfaces
    # the full turn-union so the user sees every query the model ran.
    queries: list[str] = Field(default_factory=list)


# Статус фонового ingest-таска для одного paper. Отдельно от IngestUploadResponse:
# та схема требует непустой task_id и IngestStatus без варианта "none", а здесь
# нужно уметь выразить "задачи ещё не было" (все поля конструируются по умолчанию).
class PaperIngestStatusPublic(SQLModel):
    status: str = "none"
    progress: float = Field(default=0.0, ge=0, le=1)
    stage_name: str | None = None
    error: str | None = None
