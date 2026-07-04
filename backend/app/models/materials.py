import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import DateTime
from sqlmodel import JSON, Column, Field, SQLModel

from app.models.common import get_datetime_utc

EMBEDDING_DIM = 768


class MaterialType(StrEnum):
    ALLOY = "alloy"
    COMPOUND = "compound"
    PURE_METAL = "pure_metal"


# Database model, table `experiments.materials` (SPEC_V3 §4)
class Material(SQLModel, table=True):
    __tablename__ = "materials"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    material_type: MaterialType
    formula: str | None = None
    composition: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    smiles: str | None = None
    embedding: list[float] | None = Field(
        default=None, sa_column=Column(Vector(EMBEDDING_DIM))
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class MaterialPublic(SQLModel):
    id: uuid.UUID
    name: str
    material_type: MaterialType
    formula: str | None = None
    composition: dict[str, Any] | None = None
    smiles: str | None = None
    created_at: datetime | None = None
