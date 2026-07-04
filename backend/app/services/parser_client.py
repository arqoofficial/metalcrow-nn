"""HTTP client for the nornickel-2026-parser service — single file source."""

from __future__ import annotations

import time
from datetime import datetime
from enum import Enum

from urllib.parse import unquote

import httpx
from pydantic import BaseModel

from app.core.config import settings


def _path_from_header(value: str) -> str:
    return unquote(value)


class ParserError(RuntimeError):
    """Raised when the parser rejects a request or a stage fails."""


class ProcessingStatus(str, Enum):
    pending = "pending"
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class UploadResponse(BaseModel):
    requested_path: str
    resolved_path: str
    is_final: bool


class ProcessResponse(BaseModel):
    requested_path: str
    resolved_path: str
    enforce: bool
    status: ProcessingStatus


class StageStatus(BaseModel):
    stage: str
    status: ProcessingStatus
    okf_path: str | None = None


class FileStatusResponse(BaseModel):
    requested_path: str
    resolved_path: str
    overall_status: ProcessingStatus
    stages: list[StageStatus]


class RawFileResponse(BaseModel):
    data: bytes
    content_type: str
    filename: str
    resolved_path: str


class StatisticsResponse(BaseModel):
    total_raw_files: int
    stage0_done: int
    stage1_done: int
    coverage_ratio: float


class RawFileListItem(BaseModel):
    path: str
    filename: str
    stage0_done: bool
    stage1_done: bool


class RawFileListResponse(BaseModel):
    data: list[RawFileListItem]
    count: int
    offset: int
    limit: int


class FileTreeNode(BaseModel):
    name: str
    type: str
    children: list["FileTreeNode"] = []


class FileTreeResponse(BaseModel):
    requested_root: str
    resolved_root: str
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None = None
    warnings: list[dict[str, str]] = []
    generated_at: datetime
    tree: FileTreeNode


FileTreeNode.model_rebuild()


def _client() -> httpx.Client:
    return httpx.Client(base_url=settings.PARSER_URL, timeout=settings.PARSER_TIMEOUT_S)


def upload(logical_path: str, filename: str, data: bytes) -> UploadResponse:
    """POST /api/v1/files/upload — write raw bytes under a logical path."""
    with _client() as client:
        resp = client.post(
            "/api/v1/files/upload",
            files={"file": (filename, data)},
            data={"path": logical_path},
        )
    if resp.status_code >= 400:
        raise ParserError(f"upload failed ({resp.status_code}): {resp.text}")
    return UploadResponse.model_validate(resp.json())


def enqueue_process(resolved_path: str, *, enforce: bool = False) -> ProcessResponse:
    """POST /api/v1/files/process — enqueue stage-0 Docling job."""
    deadline = time.monotonic() + settings.PARSER_UPLOAD_WAIT_S
    with _client() as client:
        while True:
            resp = client.post(
                "/api/v1/files/process",
                json={"path": resolved_path, "enforce": enforce},
            )
            if resp.status_code == 404 and time.monotonic() < deadline:
                time.sleep(settings.PARSER_POLL_INTERVAL_S)
                continue
            break
    if resp.status_code >= 400:
        raise ParserError(f"process failed ({resp.status_code}): {resp.text}")
    return ProcessResponse.model_validate(resp.json())


def get_status(resolved_path: str) -> FileStatusResponse:
    """GET /api/v1/files/status — per-stage pipeline status for a file."""
    with _client() as client:
        resp = client.get("/api/v1/files/status", params={"path": resolved_path})
    if resp.status_code >= 400:
        raise ParserError(f"status failed ({resp.status_code}): {resp.text}")
    return FileStatusResponse.model_validate(resp.json())


def fetch_markdown(okf_path: str) -> str:
    """GET /api/v1/markdown — download OKF markdown, stripping YAML frontmatter."""
    with _client() as client:
        resp = client.get("/api/v1/markdown", params={"okf_path": okf_path})
    if resp.status_code >= 400:
        raise ParserError(f"markdown fetch failed ({resp.status_code}): {resp.text}")
    return _strip_frontmatter(resp.text)


def get_statistics() -> StatisticsResponse:
    """GET /api/v1/statistics — pipeline coverage over all raw files on disk."""
    with _client() as client:
        resp = client.get("/api/v1/statistics")
    if resp.status_code >= 400:
        raise ParserError(f"statistics failed ({resp.status_code}): {resp.text}")
    return StatisticsResponse.model_validate(resp.json())


def fetch_raw(path: str) -> RawFileResponse:
    """GET /api/v1/files/raw — download original raw document bytes."""
    with _client() as client:
        resp = client.get("/api/v1/files/raw", params={"path": path})
    if resp.status_code >= 400:
        raise ParserError(f"raw fetch failed ({resp.status_code}): {resp.text}")
    content_type = resp.headers.get("content-type", "application/octet-stream")
    resolved_path = _path_from_header(resp.headers.get("X-Resolved-Path", path))
    filename = path.rsplit("/", 1)[-1] if "/" in path else path
    return RawFileResponse(
        data=resp.content,
        content_type=content_type,
        filename=filename,
        resolved_path=resolved_path,
    )


def fetch_tree(
    *,
    root: str = "",
    max_depth: int = 6,
    include_files: bool = True,
    include_dirs: bool = True,
    offset: int = 0,
    limit: int = 10,
) -> FileTreeResponse:
    """GET /api/v1/files/tree — paginated SHARED/ subtree listing."""
    with _client() as client:
        resp = client.get(
            "/api/v1/files/tree",
            params={
                "root": root,
                "max_depth": max_depth,
                "include_files": include_files,
                "include_dirs": include_dirs,
                "offset": offset,
                "limit": limit,
            },
        )
    if resp.status_code >= 400:
        raise ParserError(f"tree fetch failed ({resp.status_code}): {resp.text}")
    return FileTreeResponse.model_validate(resp.json())


def list_raw_files(
    *,
    source: str = "RAW_DATA",
    search: str = "",
    extension: str = ".pdf",
    unparsed_only: bool = True,
    offset: int = 0,
    limit: int = 10,
) -> RawFileListResponse:
    """GET /api/v1/files/list — flat catalog of concrete raw files."""
    with _client() as client:
        resp = client.get(
            "/api/v1/files/list",
            params={
                "source": source,
                "search": search,
                "extension": extension,
                "unparsed_only": unparsed_only,
                "offset": offset,
                "limit": limit,
            },
        )
    if resp.status_code >= 400:
        raise ParserError(f"list failed ({resp.status_code}): {resp.text}")
    return RawFileListResponse.model_validate(resp.json())


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n")
    return text
