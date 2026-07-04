import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum as SAEnum
from sqlmodel import JSON, Column, Field, SQLModel

from app.models.common import get_datetime_utc


class IngestStatus(StrEnum):
    QUEUED = "queued"
    PARSE = "parse"
    NORMALIZE = "normalize"
    DEDUP_LINK = "dedup_link"
    LOAD = "load"
    BUILD_FLAT = "build_flat"
    EMBED = "embed"
    SYNC_NEO4J = "sync_neo4j"
    BUILD_WIKI = "build_wiki"
    DONE = "done"
    ERROR = "error"


# Database model, table `experiments.ingest_tasks` (SPEC_V3 §7, Приложение D.6)
# Источник данных для GET /api/v1/ingest/status/{task_id} — не Celery result backend,
# а эта Postgres-таблица, обновляемая каждым таском 9-стадийного pipeline.
class IngestTask(SQLModel, table=True):
    __tablename__ = "ingest_tasks"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    status: IngestStatus = Field(
        default=IngestStatus.QUEUED,
        sa_column=Column(
            SAEnum(
                IngestStatus,
                name="ingeststatus",
                values_callable=lambda enum: [member.value for member in enum],
            ),
            nullable=False,
        ),
    )
    progress: float = 0.0
    stage_name: str | None = None
    error: str | None = None
    # Хранится как list[str], а не list[uuid.UUID]: сырая JSON-колонка сериализуется
    # стандартным json.dumps (через psycopg), который не умеет UUID -> JSON.
    document_ids: list[str] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
