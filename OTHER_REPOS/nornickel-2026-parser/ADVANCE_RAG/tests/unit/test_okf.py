"""OKF parser unit tests."""

from app.data.okf import OkfParseError, okf_meta_for_response, parse_okf_content

VALID_OKF = """---
type: report
title: Nickel Production Forecast
description: Q1 outlook
resource: okf://reports/q1
tags:
  - nickel
timestamp: "2026-01-15T10:00:00Z"
---

Body content here.
"""


def test_valid_metadata_parsed_correctly() -> None:
    result = parse_okf_content(VALID_OKF)
    assert not isinstance(result, OkfParseError)
    assert result.meta.type == "report"
    assert result.meta.title == "Nickel Production Forecast"
    assert result.body == "Body content here."


def test_missing_required_type_rejected() -> None:
    content = "---\ntitle: No Type\n---\nBody"
    result = parse_okf_content(content)
    assert isinstance(result, OkfParseError)
    assert result.code == "missing_type"


def test_malformed_yaml_rejected() -> None:
    content = "---\ntype: [unclosed\n---\nBody"
    result = parse_okf_content(content)
    assert isinstance(result, OkfParseError)
    assert result.code == "malformed_yaml"


def test_metadata_extraction_for_response_contract() -> None:
    parsed = parse_okf_content(VALID_OKF)
    assert not isinstance(parsed, OkfParseError)
    payload = okf_meta_for_response(parsed.meta)
    assert payload["type"] == "report"
    assert "title" in payload
