"""Redis queue job messages."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class QueueStage(str, Enum):
    raw2docling_raw = "raw2docling_raw"
    docling_raw2docling_clean00 = "docling_raw2docling_clean00"


class QueueJob(BaseModel):
    """Single pipeline job on a stage-specific Redis list."""

    job_id: str = Field(default_factory=lambda: str(uuid4()))
    requested_path: str = Field(
        ...,
        description="Path from API request (logical or concrete).",
    )
    resolved_path: str = Field(
        ...,
        description="Concrete raw path under SHARED/, e.g. UPLOAD_DATA/reports/q1__v02.pdf",
    )
    stage: QueueStage
    enforce: bool = False
    enqueued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, payload: str) -> QueueJob:
        return cls.model_validate_json(payload)
