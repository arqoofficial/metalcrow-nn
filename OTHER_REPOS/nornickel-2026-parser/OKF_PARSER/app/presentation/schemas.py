"""API request and response schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ProcessingStatus(str, Enum):
    pending = "pending"
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class ProcessRequest(BaseModel):
    path: str = Field(
        ...,
        description=(
            "Logical path (e.g. `reports/q1.pdf`) or concrete path under "
            "`UPLOAD_DATA/` or `RAW_DATA/`."
        ),
        examples=["reports/q1.pdf", "RAW_DATA/journals/CM_07_09.pdf"],
    )
    enforce: bool = Field(
        False,
        description="When `true`, enqueue stage-0 even if OKF output already exists (overwrite).",
    )


class ProcessResponse(BaseModel):
    requested_path: str = Field(..., description="Path as submitted in the request.")
    resolved_path: str = Field(
        ...,
        description="Concrete raw path relative to `SHARED/` selected by exact-path resolution.",
    )
    enforce: bool
    status: ProcessingStatus = Field(..., description="Always `queued` on successful acceptance.")
    queued_at: datetime = Field(..., description="UTC timestamp when the job was enqueued.")


class UploadResponse(BaseModel):
    requested_path: str = Field(..., description="Logical upload path from the form field.")
    resolved_path: str = Field(
        ...,
        description="Concrete path written under `UPLOAD_DATA/` (simple name or next `__vNN`).",
    )
    is_final: bool = Field(
        ...,
        description="`true` when the target file already exists on disk at response time.",
    )


class ReindexRequest(BaseModel):
    enforce: bool = Field(
        False,
        description=(
            "When `true`, enqueue stage-0 for files that already have OKF output. "
            "When `false`, skip files with existing stage-0 output."
        ),
    )


class ReindexResponse(BaseModel):
    enqueued: int = Field(..., description="Number of stage-0 jobs accepted into the queue.")
    stage1_enqueued: int = Field(
        ...,
        description="Number of stage-1 backfill jobs enqueued for files with stage-0 output only.",
    )


class StageStatus(BaseModel):
    stage: str = Field(..., description="Pipeline stage id: `docling_raw` or `docling_clean00`.")
    status: ProcessingStatus
    okf_path: Optional[str] = Field(
        None,
        description="Relative OKF path under `SHARED/` for this stage, when applicable.",
    )


class FileStatusResponse(BaseModel):
    requested_path: str
    resolved_path: str = Field(..., description="Exact raw file key under `SHARED/`.")
    is_final: bool = Field(..., description="Reserved; always `true` in current API.")
    overall_status: ProcessingStatus = Field(
        ...,
        description="Worst at stage statuses; `failed` ranks below `processing`.",
    )
    stages: list[StageStatus]


class StatisticsResponse(BaseModel):
    total_raw_files: int = Field(
        ...,
        description="Count of Docling-eligible raw files under `UPLOAD_DATA/` and `RAW_DATA/`.",
    )
    stage0_done: int = Field(..., description="Raw files with stage-0 OKF on disk.")
    stage1_done: int = Field(..., description="Raw files with stage-1 OKF on disk.")
    coverage_ratio: float = Field(..., description="`stage1_done / total_raw_files`.")


class FileTreeNode(BaseModel):
    name: str
    type: str = Field(..., description="`dir` or `file`.")
    children: list[FileTreeNode] = Field(default_factory=list)


class FileTreeResponse(BaseModel):
    requested_root: str = Field(..., description="`root` query param as submitted.")
    resolved_root: str = Field(..., description="Normalized subtree path relative to `SHARED/`.")
    offset: int
    limit: int
    has_more: bool
    next_offset: Optional[int] = None
    warnings: list[dict[str, str]] = Field(
        default_factory=list,
        description="Non-fatal normalization notices (e.g. collapsed separators).",
    )
    generated_at: datetime
    tree: FileTreeNode


class HealthResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})

    status: str = Field(..., description="Always `ok` when the process is alive.")


class ReadyResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ready"}]})

    status: str = Field(..., description="Always `ready` when Redis ping succeeds.")


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Human-readable error message.")
