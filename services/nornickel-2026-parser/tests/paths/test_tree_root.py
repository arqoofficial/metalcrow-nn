"""Step 02 - subtree root normalization and validation."""

import pytest

from app.paths import PathValidationError, normalize_subtree_root, parse_concrete_raw_path


def test_subtree_root_normalization() -> None:
    result = normalize_subtree_root("//UPLOAD_DATA//reports/./")
    assert result.normalized == "UPLOAD_DATA/reports"
    assert any(w.code == "ROOT_NORMALIZED" for w in result.warnings)

    shared_prefixed = normalize_subtree_root("SHARED/UPLOAD_DATA")
    assert shared_prefixed.normalized == "UPLOAD_DATA"
    assert shared_prefixed.warnings


def test_outside_shared_rejected() -> None:
    with pytest.raises(PathValidationError, match="escapes SHARED"):
        normalize_subtree_root("../outside")

    with pytest.raises(PathValidationError, match="escapes SHARED"):
        normalize_subtree_root("UPLOAD_DATA/../../outside")


def test_malformed_version_rejected() -> None:
    with pytest.raises(PathValidationError, match="malformed version token"):
        parse_concrete_raw_path("UPLOAD_DATA/reports/q1__vXX.pdf")
