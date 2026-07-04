"""Flat listing of concrete raw files under a SHARED/ source prefix."""

from __future__ import annotations

from pathlib import Path

from app.paths import SOURCE_RAW, raw_to_stage0_okf, raw_to_stage1_okf
from app.presentation.schemas import RawFileListItem
from app.services.path_resolution import list_raw_concrete_paths


def list_raw_files(
    shared_root: str,
    *,
    source: str = SOURCE_RAW,
    search: str = "",
    extension: str = ".pdf",
    unparsed_only: bool = True,
    offset: int = 0,
    limit: int = 10,
) -> tuple[list[RawFileListItem], int]:
    paths = [
        item
        for item in list_raw_concrete_paths(shared_root)
        if item.startswith(f"{source}/")
    ]
    if extension:
        normalized_ext = extension.lower()
        if not normalized_ext.startswith("."):
            normalized_ext = f".{normalized_ext}"
        paths = [item for item in paths if item.lower().endswith(normalized_ext)]
    if search:
        needle = search.casefold()
        paths = [item for item in paths if needle in item.casefold()]

    root = Path(shared_root)
    items: list[RawFileListItem] = []
    for path in sorted(paths):
        stage0_done = (root / raw_to_stage0_okf(path)).is_file()
        if unparsed_only and stage0_done:
            continue
        items.append(
            RawFileListItem(
                path=path,
                filename=Path(path).name,
                stage0_done=stage0_done,
                stage1_done=(root / raw_to_stage1_okf(path)).is_file(),
            )
        )

    total = len(items)
    page = items[offset : offset + limit]
    return page, total
