import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel

from app.models.common import get_datetime_utc


class LitStage(StrEnum):
    SEARCHING = "searching"
    FETCHING = "fetching"
    READING = "reading"
    DONE = "done"
    FAILED = "failed"


class FetchStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class FulltextStatus(StrEnum):
    NONE = "none"
    ADDED = "added"
    FAILED = "failed"


class LitIngestStatus(StrEnum):
    NONE = "none"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# Database model, table `experiments.literature_searches` (litsearch -> chat
# integration, KG_EXTRACTION_SPEC/companion spec §4.3)
class LiteratureSearch(SQLModel, table=True):
    __tablename__ = "literature_searches"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(
        foreign_key="chat_session.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    question: str
    stage: LitStage = Field(default=LitStage.SEARCHING)
    round: int = Field(default=0)
    followup_of: uuid.UUID | None = None
    followup_search_id: uuid.UUID | None = None
    error: str | None = None
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# Database model, table `experiments.literature_papers` (litsearch -> chat
# integration, KG_EXTRACTION_SPEC/companion spec §4.3)
class LiteraturePaper(SQLModel, table=True):
    __tablename__ = "literature_papers"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    search_id: uuid.UUID = Field(
        foreign_key="experiments.literature_searches.id",
        nullable=False,
        ondelete="CASCADE",
        index=True,
    )
    doi: str | None = None
    title: str
    authors: str
    year: int | None = None
    abstract: str
    pdf_url: str | None = None
    citation_count: int | None = None
    fetch_status: FetchStatus = Field(default=FetchStatus.PENDING)
    fetch_job_id: str | None = None
    object_key: str | None = None
    fulltext_status: FulltextStatus = Field(default=FulltextStatus.NONE)
    fulltext_chars: int = Field(default=0)
    # Extracted full text, persisted so litsearch_read_fulltext repeat calls
    # don't re-download+re-extract (spec §2.3). NULL until a fetch succeeds.
    fulltext_text: str | None = None
    ingest_status: LitIngestStatus = Field(default=LitIngestStatus.NONE)
    ingest_task_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="experiments.documents.id",
        unique=True,
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
