"""Filesystem tree scanning for GET /api/v1/files/tree."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.paths import PathValidationError, normalize_subtree_root
from app.presentation.schemas import FileTreeNode, FileTreeResponse
from app.services.path_resolution import is_upload_lock_or_worker_lock


def _is_visible(name: str) -> bool:
    if name.startswith("."):
        return False
    if is_upload_lock_or_worker_lock(name):
        return False
    return True


def _scan_directory(
    directory: Path,
    *,
    remaining_depth: int,
    include_files: bool,
    include_dirs: bool,
) -> list[FileTreeNode]:
    if remaining_depth < 0 or not directory.is_dir():
        return []

    entries = sorted(
        (entry for entry in directory.iterdir() if _is_visible(entry.name)),
        key=lambda entry: entry.name,
    )
    nodes: list[FileTreeNode] = []
    for entry in entries:
        if entry.is_symlink():
            if include_files:
                nodes.append(FileTreeNode(name=entry.name, type="file", children=[]))
            continue
        if entry.is_dir():
            if not include_dirs:
                continue
            children = (
                _scan_directory(
                    entry,
                    remaining_depth=remaining_depth - 1,
                    include_files=include_files,
                    include_dirs=include_dirs,
                )
                if remaining_depth > 0
                else []
            )
            nodes.append(FileTreeNode(name=entry.name, type="dir", children=children))
            continue
        if entry.is_file() and include_files:
            nodes.append(FileTreeNode(name=entry.name, type="file", children=[]))
    return nodes


def _wrap_under_shared(resolved_root: str, children: list[FileTreeNode]) -> FileTreeNode:
    parts = resolved_root.split("/")
    node = FileTreeNode(name=parts[-1], type="dir", children=children)
    for part in reversed(parts[:-1]):
        node = FileTreeNode(name=part, type="dir", children=[node])
    return FileTreeNode(name="SHARED", type="dir", children=[node])


class TreeValidationError(ValueError):
    code: int

    def __init__(self, message: str, code: int = 400) -> None:
        super().__init__(message)
        self.code = code


def build_tree_response(
    *,
    shared_root: str,
    root: str = "",
    max_depth: int = 6,
    include_files: bool = True,
    include_dirs: bool = True,
    offset: int = 0,
    limit: int = 10,
) -> FileTreeResponse:
    if max_depth > 10 or limit > 1000 or offset < 0 or max_depth < 0:
        raise TreeValidationError("invalid tree query bounds", code=400)

    try:
        normalized = normalize_subtree_root(root)
    except PathValidationError as exc:
        raise TreeValidationError(str(exc), code=400) from exc

    resolved_root = normalized.normalized
    target = Path(shared_root) / resolved_root if resolved_root else Path(shared_root)
    if not target.is_dir():
        raise TreeValidationError("requested subtree does not exist", code=404)

    all_children = _scan_directory(
        target,
        remaining_depth=max_depth,
        include_files=include_files,
        include_dirs=include_dirs,
    )
    paged_children = all_children[offset : offset + limit]
    has_more = offset + limit < len(all_children)
    next_offset = offset + limit if has_more else None

    if resolved_root == "":
        tree = FileTreeNode(name="SHARED", type="dir", children=paged_children)
    else:
        tree = _wrap_under_shared(resolved_root, paged_children)

    warnings = [warning.model_dump() for warning in normalized.warnings]
    return FileTreeResponse(
        requested_root=root,
        resolved_root=resolved_root,
        offset=offset,
        limit=limit,
        has_more=has_more,
        next_offset=next_offset,
        warnings=warnings,
        generated_at=datetime.now(timezone.utc),
        tree=tree,
    )
