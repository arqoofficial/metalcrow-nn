"""In-memory stub for nornickel-2026-parser HTTP client — tests without a running parser."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services import parser_client


@dataclass
class FakeParser:
    uploads: dict[str, bytes] = field(default_factory=dict)
    markdowns: dict[str, str] = field(default_factory=dict)
    stage0_done_paths: set[str] = field(default_factory=set)
    tree: parser_client.FileTreeNode | None = None

    def upload(self, logical_path: str, filename: str, data: bytes) -> parser_client.UploadResponse:
        resolved = f"UPLOAD_DATA/{logical_path}"
        self.uploads[resolved] = data
        return parser_client.UploadResponse(
            requested_path=logical_path,
            resolved_path=resolved,
            is_final=True,
        )

    def enqueue_process(
        self, resolved_path: str, *, enforce: bool = False
    ) -> parser_client.ProcessResponse:
        _ = enforce
        return parser_client.ProcessResponse(
            requested_path=resolved_path,
            resolved_path=resolved_path,
            enforce=enforce,
            status=parser_client.ProcessingStatus.queued,
        )

    def get_status(self, resolved_path: str) -> parser_client.FileStatusResponse:
        return parser_client.FileStatusResponse(
            requested_path=resolved_path,
            resolved_path=resolved_path,
            overall_status=parser_client.ProcessingStatus.pending,
            stages=[],
        )

    def fetch_markdown(self, okf_path: str) -> str:
        if okf_path not in self.markdowns:
            raise parser_client.ParserError(f"markdown not found: {okf_path}")
        return self.markdowns[okf_path]

    def fetch_raw(self, path: str) -> parser_client.RawFileResponse:
        candidates = [path]
        if not path.startswith(("UPLOAD_DATA/", "RAW_DATA/")):
            candidates.append(f"UPLOAD_DATA/{path}")
        data = None
        resolved = path
        for candidate in candidates:
            data = self.uploads.get(candidate)
            if data is not None:
                resolved = candidate
                break
        if data is None:
            raise parser_client.ParserError(f"raw not found: {path}")
        filename = resolved.rsplit("/", 1)[-1]
        return parser_client.RawFileResponse(
            data=data,
            content_type="application/octet-stream",
            filename=filename,
            resolved_path=resolved,
        )

    def get_statistics(self) -> parser_client.StatisticsResponse:
        total = len(self.uploads)
        return parser_client.StatisticsResponse(
            total_raw_files=total,
            stage0_done=0,
            stage1_done=0,
            coverage_ratio=0.0,
        )

    def fetch_tree(
        self,
        *,
        root: str = "",
        max_depth: int = 6,
        include_files: bool = True,
        include_dirs: bool = True,
        offset: int = 0,
        limit: int = 10,
    ) -> parser_client.FileTreeResponse:
        _ = (max_depth, include_files, include_dirs)
        tree = self.tree or parser_client.FileTreeNode(
            name="01_docling_clean00",
            type="dir",
            children=[],
        )
        return parser_client.FileTreeResponse(
            requested_root=root,
            resolved_root=root or "01_docling_clean00",
            offset=offset,
            limit=limit,
            has_more=False,
            next_offset=None,
            warnings=[],
            generated_at=datetime.now(timezone.utc),
            tree=tree,
        )

    def list_raw_files(
        self,
        *,
        source: str = "RAW_DATA",
        search: str = "",
        extension: str = ".pdf",
        unparsed_only: bool = True,
        offset: int = 0,
        limit: int = 10,
    ) -> parser_client.RawFileListResponse:
        normalized_ext = extension.lower()
        if not normalized_ext.startswith("."):
            normalized_ext = f".{normalized_ext}"
        paths = sorted(
            path
            for path in self.uploads
            if path.startswith(f"{source}/") and path.lower().endswith(normalized_ext)
        )
        if search:
            needle = search.casefold()
            paths = [path for path in paths if needle in path.casefold()]
        items = []
        for path in paths:
            stage0_done = path in self.stage0_done_paths
            if unparsed_only and stage0_done:
                continue
            items.append(
                parser_client.RawFileListItem(
                    path=path,
                    filename=path.rsplit("/", 1)[-1],
                    stage0_done=stage0_done,
                    stage1_done=False,
                )
            )
        page = items[offset : offset + limit]
        return parser_client.RawFileListResponse(
            data=page,
            count=len(items),
            offset=offset,
            limit=limit,
        )
