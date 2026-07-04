import uuid
from datetime import date as date_
from datetime import datetime

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Column, Field, SQLModel, String

from app.models.common import get_datetime_utc
from app.models.materials import EMBEDDING_DIM


# Database model, table `experiments.experiments` (SPEC_V3 §4)
class Experiment(SQLModel, table=True):
    __tablename__ = "experiments"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str | None = None
    material_id: uuid.UUID | None = Field(
        default=None, foreign_key="experiments.materials.id"
    )
    regime_id: uuid.UUID | None = Field(
        default=None, foreign_key="experiments.regimes.id"
    )
    equipment_id: uuid.UUID | None = Field(
        default=None, foreign_key="experiments.equipment.id"
    )
    lab_id: uuid.UUID | None = Field(default=None, foreign_key="experiments.labs.id")
    researcher_id: uuid.UUID | None = Field(
        default=None, foreign_key="experiments.researchers.id"
    )
    date: date_ | None = None
    description: str | None = None
    source_anchor: str | None = None  # идентификатор блока в источнике
    grouping_key: str | None = None  # (date, lab, researcher, material, regime) hash
    document_id: uuid.UUID | None = Field(
        default=None, foreign_key="experiments.documents.id"
    )
    source_page: int | None = None
    source_paragraph: str | None = None
    tags: list[str] | None = Field(default=None, sa_column=Column(ARRAY(String)))
    embedding: list[float] | None = Field(
        default=None, sa_column=Column(Vector(EMBEDDING_DIM))
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# Database model, table `experiments.results` (SPEC_V3 §4)
class Result(SQLModel, table=True):
    __tablename__ = "results"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    experiment_id: uuid.UUID | None = Field(
        default=None, foreign_key="experiments.experiments.id"
    )
    property_id: uuid.UUID | None = Field(
        default=None, foreign_key="experiments.properties.id"
    )
    value: float | None = None
    unit: str | None = None
    uncertainty: float | None = None
    proof_ref: str | None = None
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
