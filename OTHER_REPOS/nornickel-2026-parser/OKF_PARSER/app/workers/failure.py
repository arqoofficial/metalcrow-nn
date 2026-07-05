"""Persist pipeline failures for status reporting."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ERROR_DIR = ".pipeline_errors"


def error_marker_path(shared_root: str, stage: str, resolved_path: str) -> Path:
    safe_name = resolved_path.replace("/", "__")
    return Path(shared_root) / ERROR_DIR / stage / f"{safe_name}.json"


def record_failure(
    shared_root: str,
    *,
    stage: str,
    resolved_path: str,
    worker: str,
    error: str,
) -> None:
    path = error_marker_path(shared_root, stage, resolved_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage": stage,
        "resolved_path": resolved_path,
        "worker": worker,
        "error": error,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clear_failure(shared_root: str, stage: str, resolved_path: str) -> None:
    path = error_marker_path(shared_root, stage, resolved_path)
    if path.is_file():
        path.unlink()


def has_failure(shared_root: str, stage: str, resolved_path: str) -> bool:
    return error_marker_path(shared_root, stage, resolved_path).is_file()
