"""Step 00 - implementation baseline and docs lock tests."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION_NOTES = REPO_ROOT / "IMPLEMENTATION_NOTES.md"

CONTRACT_DOCS = [
    "docs/SPECIFICATION.md",
    "docs/LAYER_PRESENTATION.md",
    "docs/LAYER_SERVICES.md",
    "docs/LAYER_DATA.md",
    "docs/LAYER_CONFIG.md",
    "docs/ADMIN_PANEL.md",
    "docs/LAYER_INFRASTRUCTURE.md",
]

DECISION_LOCK_MARKERS = [
    ("outside SHARED -> 400", ["outside `SHARED`", "400"]),
    ("limit <= 1000", ["limit <= 1000"]),
    ("max_depth <= 10", ["max_depth <= 10"]),
    ("hidden/lock exclusion", ["hidden", "lock"]),
    ("no symlink traversal", ["symlink", "not follow"]),
    ("QueueJob field: job_id", ["job_id"]),
    ("QueueJob field: requested_path", ["requested_path"]),
    ("QueueJob field: resolved_path", ["resolved_path"]),
    ("QueueJob field: stage", ["stage"]),
    ("QueueJob field: enforce", ["enforce"]),
    ("QueueJob field: enqueued_at", ["enqueued_at"]),
    ("version token __vNN", ["__vNN"]),
]


def test_implementation_notes_exists() -> None:
    assert IMPLEMENTATION_NOTES.is_file(), "IMPLEMENTATION_NOTES.md must exist at repo root"


def test_implementation_notes_contains_decision_lock() -> None:
    content = IMPLEMENTATION_NOTES.read_text(encoding="utf-8")
    assert "Decision Lock" in content

    missing: list[str] = []
    for label, markers in DECISION_LOCK_MARKERS:
        if not any(marker in content for marker in markers):
            missing.append(label)

    assert not missing, f"IMPLEMENTATION_NOTES.md missing decision lock items: {missing}"


@pytest.mark.parametrize("relative_path", CONTRACT_DOCS)
def test_referenced_docs_exist(relative_path: str) -> None:
    doc_path = REPO_ROOT / relative_path
    assert doc_path.is_file(), f"Contract doc must exist: {relative_path}"
