"""Project-specific OKF frontmatter extensions for the nornickel parser."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.data.okf_standard import OkfFrontmatterStandard

PARSER_OKF_TYPE = "Parsed Document"


class DataSource(str, Enum):
    upload_data = "UPLOAD_DATA"
    raw_data = "RAW_DATA"


class PipelineStageId(str, Enum):
    docling_raw = "docling_raw"
    docling_clean00 = "docling_clean00"


class ParserOkfRawRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="Canonical raw path, e.g. reports/2024/q1.pdf")
    source: DataSource
    absolute_path: str = Field(
        ...,
        description="Path under SHARED/, e.g. UPLOAD_DATA/reports/2024/q1.pdf",
    )
    sha256: str = Field(..., min_length=64, max_length=64)
    media_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)

    @field_validator("sha256")
    @classmethod
    def lowercase_hex(cls, value: str) -> str:
        normalized = value.lower()
        if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return normalized


class ParserOkfStageRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: PipelineStageId
    folder: str = Field(..., description="e.g. 00_docling_raw")
    sequence: int | None = Field(default=None, ge=0)


class ParserOkfPipelineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    docling_version: str | None = None
    cleaner_version: str | None = None
    worker: str | None = None


class ParserOkfGitInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commit: str | None = None
    version_label: str | None = None


class ParserOkfExtensionMixin(BaseModel):
    """Project extension fields stored in OKF frontmatter (producer-defined §4.1)."""

    model_config = ConfigDict(extra="forbid")

    raw: ParserOkfRawRef
    stage: ParserOkfStageRef
    processed_at: datetime
    pipeline: ParserOkfPipelineInfo | None = None
    git: ParserOkfGitInfo | None = None


class ParserOkfFrontmatter(OkfFrontmatterStandard, ParserOkfExtensionMixin):
    """OKF v0.1 frontmatter + nornickel parser pipeline metadata."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ParserOkfDocument(BaseModel):
    """Full parser OKF file: frontmatter + markdown body."""

    model_config = ConfigDict(extra="forbid")

    frontmatter: ParserOkfFrontmatter
    body: str = Field(..., description="Markdown content after frontmatter.")


def is_okf_current(frontmatter: ParserOkfFrontmatter, raw_sha256: str) -> bool:
    return frontmatter.raw.sha256 == raw_sha256.lower()
