import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.documents import ProcessingLevel
from app.models.ingest import IngestStatus


class IngestUploadResponse(SQLModel):
    task_id: uuid.UUID
    status: IngestStatus
    progress: float = Field(default=0.0, ge=0, le=1)
    stage_name: str | None = None
    error: str | None = None


class IngestUploadBatchResponse(SQLModel):
    """Ответ upload: L0 в MinIO/Postgres + сразу поставленная в очередь L1-обработка."""

    data: list["DocumentFileSummary"]
    count: int
    task_id: uuid.UUID | None = None


class IngestRunRequest(SQLModel):
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    level: ProcessingLevel = ProcessingLevel.L1


class DocumentFileSummary(SQLModel):
    id: uuid.UUID
    filename: str
    mime_type: str | None = None
    processing_level: ProcessingLevel
    okf_raw_path: str | None = None
    uploaded_at: datetime | None = None
    # Статус последней ingest-задачи для этого документа (для live-индикации в UI)
    latest_task_status: IngestStatus | None = None
    latest_task_progress: float | None = None
    latest_task_stage: str | None = None
    latest_task_error: str | None = None


class IngestFilesResponse(SQLModel):
    data: list[DocumentFileSummary]
    count: int


class IngestFileHistoryItem(SQLModel):
    task_id: uuid.UUID
    status: IngestStatus
    stage_name: str | None = None
    progress: float
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class IngestFileDetailResponse(SQLModel):
    document: DocumentFileSummary
    history: list[IngestFileHistoryItem]


class ProcessingLevelCoverage(SQLModel):
    level: ProcessingLevel
    count: int
    percent: float


class AdminCoverageResponse(SQLModel):
    total_files: int
    by_level: list[ProcessingLevelCoverage]


class RawDataFileSummary(SQLModel):
    path: str
    filename: str
    stage0_done: bool
    stage1_done: bool
    document_id: uuid.UUID | None = None
    processing_level: ProcessingLevel | None = None
    latest_task_status: IngestStatus | None = None


class RawDataFilesResponse(SQLModel):
    data: list[RawDataFileSummary]
    count: int
    offset: int
    limit: int


class ParseRawDataFileRequest(SQLModel):
    path: str = Field(
        ...,
        description="Concrete parser path under RAW_DATA/, e.g. RAW_DATA/Доклады/report.pdf",
    )
    enforce: bool = Field(
        default=False,
        description="Re-enqueue L1 parsing even when the document already reached L1.",
    )
