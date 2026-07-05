"""Path boundary unit tests."""

from pathlib import Path

import pytest

from app.config.settings import QueryConfig, SharedConfig
from app.data.paths import (
    PathValidationError,
    ResolvedPath,
    normalize_relative_path,
    resolve_path_in_shared,
    resolve_shared_root,
    resolve_source_subfolder,
)


@pytest.fixture
def shared_root(tmp_path: Path) -> Path:
    root = tmp_path / "SHARED"
    (root / "01_docling_clean00" / "reports").mkdir(parents=True)
    (root / "00_docling_raw").mkdir(parents=True)
    file_path = root / "01_docling_clean00" / "reports" / "doc.okf.md"
    file_path.write_text("body", encoding="utf-8")
    return root


def test_valid_relative_path_resolves_inside_shared(shared_root: Path) -> None:
    result = resolve_path_in_shared(
        shared_root,
        "01_docling_clean00/reports/doc.okf.md",
        ["00_docling_raw", "01_docling_clean00"],
    )
    assert isinstance(result, ResolvedPath)
    assert result.source_subfolder == "01_docling_clean00"
    assert result.relative_in_subfolder == "reports/doc.okf.md"


def test_traversal_attempts_rejected(shared_root: Path) -> None:
    with pytest.raises(ValueError):
        normalize_relative_path("../etc/passwd")
    result = resolve_path_in_shared(
        shared_root,
        "../../etc/passwd",
        ["01_docling_clean00"],
    )
    assert isinstance(result, PathValidationError)
    assert result.code == "traversal_rejected"


def test_non_allowed_subfolder_rejected(shared_root: Path) -> None:
    result = resolve_path_in_shared(
        shared_root,
        "02_other/doc.okf.md",
        ["01_docling_clean00"],
    )
    assert isinstance(result, PathValidationError)
    assert result.code == "subfolder_not_allowed"


def test_resolve_source_subfolder_default() -> None:
    cfg = QueryConfig()
    assert resolve_source_subfolder(cfg, None) == "01_docling_clean00"


def test_resolve_source_subfolder_rejects_unknown() -> None:
    cfg = QueryConfig()
    result = resolve_source_subfolder(cfg, "forbidden")
    assert isinstance(result, PathValidationError)


def test_shared_root_resolution(tmp_path: Path) -> None:
    shared = SharedConfig(root=str(tmp_path / "SHARED"))
    assert resolve_shared_root(shared, tmp_path) == (tmp_path / "SHARED").resolve()
