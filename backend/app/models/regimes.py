import uuid
from typing import Any

from sqlmodel import JSON, Column, Field, SQLModel


# Database model, table `experiments.regimes` (SPEC_V3 §4)
class Regime(SQLModel, table=True):
    __tablename__ = "regimes"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    temperature: float | None = None  # Kelvin
    pressure: float | None = None  # Pa
    duration: float | None = None  # seconds
    medium: str | None = None
    # [{step, temperature, duration, ...}]
    steps: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))


class RegimePublic(SQLModel):
    id: uuid.UUID
    temperature: float | None = None
    pressure: float | None = None
    duration: float | None = None
    medium: str | None = None
    steps: list[dict[str, Any]] | None = None
