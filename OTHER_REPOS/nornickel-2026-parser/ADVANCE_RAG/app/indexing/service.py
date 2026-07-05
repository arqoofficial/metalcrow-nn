"""Document indexing service."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.config.settings import RuntimeConfig
from app.data.chroma_adapter import ChromaAdapter
from app.data.okf import OkfParseError, parse_okf_file
from app.data.paths import (
    PathValidationError,
    ResolvedPath,
    normalize_relative_path,
    resolve_path_in_shared,
    resolve_shared_root,
)

MAX_DENSE_INDEX_CHARS = 8000


class IndexingService:
    def __init__(self, runtime: RuntimeConfig, chroma: ChromaAdapter, base_dir: Path) -> None:
        self._runtime = runtime
        self._chroma = chroma
        self._base_dir = base_dir
        self._shared_root = resolve_shared_root(runtime.shared, base_dir)

    def resolve_index_target(
        self,
        path: str,
        source_subfolder: str | None = None,
    ) -> ResolvedPath | PathValidationError:
        resolved = resolve_path_in_shared(
            self._shared_root,
            path,
            self._runtime.query.allowed_source_subfolders,
            source_subfolder=source_subfolder,
        )
        if isinstance(resolved, PathValidationError):
            return resolved
        if not isinstance(resolved, ResolvedPath):
            return PathValidationError(code="invalid", message="Invalid path", path=path)
        return resolved

    def index_document(
        self,
        path: str,
        source_subfolder: str | None = None,
    ) -> tuple[str, str, str] | PathValidationError | OkfParseError:
        resolved = self.resolve_index_target(path, source_subfolder=source_subfolder)
        if isinstance(resolved, PathValidationError):
            return resolved
        if not resolved.absolute.is_file():
            return PathValidationError(code="not_found", message="File not found", path=path)

        parsed = parse_okf_file(resolved.absolute)
        if isinstance(parsed, OkfParseError):
            return parsed

        document_id = hashlib.sha256(str(resolved.absolute).encode()).hexdigest()[:16]
        content = parsed.body or resolved.absolute.read_text(encoding="utf-8")
        dense_content = _trim_dense_content(content)
        self._chroma.upsert(
            document_id,
            dense_content,
            {
                "path": resolved.relative_in_subfolder,
                "source_subfolder": resolved.source_subfolder,
                "okf_type": parsed.meta.type,
                "okf_title": parsed.meta.title or "",
            },
        )
        return "indexed", resolved.relative_to_shared, resolved.source_subfolder

    def list_okf_files(self, subfolder_path: str, source_subfolder: str) -> list[Path]:
        if source_subfolder not in self._runtime.query.allowed_source_subfolders:
            return []
        try:
            normalized = normalize_relative_path(subfolder_path)
        except ValueError:
            return []

        base = (self._shared_root / source_subfolder).resolve()
        root = (base / normalized).resolve()
        try:
            root.relative_to(base)
        except ValueError:
            return []

        if not root.is_dir():
            return []
        return sorted(path for path in root.rglob("*.md") if path.is_file())


def _trim_dense_content(content: str) -> str:
    if len(content) <= MAX_DENSE_INDEX_CHARS:
        return content
    return content[:MAX_DENSE_INDEX_CHARS]
