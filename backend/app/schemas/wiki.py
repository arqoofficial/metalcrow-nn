from datetime import datetime
from typing import Literal

from sqlmodel import Field, SQLModel


WIKI_STAGE_ROOT = "01_docling_clean00"


class WikiFileTreeNode(SQLModel):
    name: str
    type: Literal["dir", "file"]
    path: str | None = Field(
        default=None,
        description="Relative path under SHARED/ for file nodes.",
    )
    children: list["WikiFileTreeNode"] = Field(default_factory=list)


class WikiTreeResponse(SQLModel):
    requested_root: str
    resolved_root: str
    generated_at: datetime
    children: list[WikiFileTreeNode] = Field(
        default_factory=list,
        description="Top-level folders inside 01_docling_clean00/ (e.g. RAW_DATA, UPLOAD_DATA).",
    )


class WikiSearchResult(SQLModel):
    okf_path: str
    title: str
    snippet: str | None = None


class WikiSearchResponse(SQLModel):
    results: list[WikiSearchResult]
    total: int


class WikiDocumentContent(SQLModel):
    okf_path: str
    title: str
    display_path: str = Field(
        description="Path relative to 01_docling_clean00/ for UI display.",
    )
    raw_path: str | None = Field(
        default=None,
        description="Concrete raw path under SHARED/ for the original document.",
    )
    markdown: str
