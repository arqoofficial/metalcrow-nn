import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel

from app.models.common import get_datetime_utc


class ProcessingLevel(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


# Database model, table `experiments.documents` (SPEC_V3 §4, SPEC_V5 §4)
class Document(SQLModel, table=True):
    __tablename__ = "documents"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    parser_path: str
    filename: str
    mime_type: str | None = None
    processing_level: ProcessingLevel = Field(default=ProcessingLevel.L0)
    okf_raw_path: str | None = None
    uploaded_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
