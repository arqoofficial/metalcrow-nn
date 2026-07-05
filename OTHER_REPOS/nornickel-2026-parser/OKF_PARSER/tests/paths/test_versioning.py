"""Step 02 - version token utilities."""

from app.paths import compare_versions, next_version, parse_version_number


def test_next_version_from_existing_set() -> None:
    assert next_version([]) == 1
    assert next_version([1, 2, 10]) == 11


def test_numeric_compare_variable_width() -> None:
    assert compare_versions(2, 10) < 0
    assert compare_versions("v2", "v10") < 0
    assert compare_versions(parse_version_number("v02"), 2) == 0
