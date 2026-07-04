"""Statistics over all concrete raw files on disk."""

from __future__ import annotations

from pathlib import Path

from app.paths import parse_concrete_raw_path, raw_to_stage1_okf
from app.presentation.schemas import StatisticsResponse
from app.services.path_resolution import list_raw_concrete_paths
from app.services.status_service import stage0_output_exists


def build_statistics(shared_root: str) -> StatisticsResponse:
    all_files = [parse_concrete_raw_path(item) for item in list_raw_concrete_paths(shared_root)]
    total = len(all_files)
    stage0_done = sum(
        1 for item in all_files if stage0_output_exists(shared_root, item.relative)
    )
    stage1_done = sum(
        1
        for item in all_files
        if Path(shared_root, raw_to_stage1_okf(item.relative)).is_file()
    )
    coverage = (stage1_done / total) if total else 0.0
    return StatisticsResponse(
        total_raw_files=total,
        stage0_done=stage0_done,
        stage1_done=stage1_done,
        coverage_ratio=coverage,
    )
