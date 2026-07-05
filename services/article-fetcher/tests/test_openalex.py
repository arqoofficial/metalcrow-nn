"""Tests for `app.openalex` — the standalone OpenAlex `/works` search client.

Covers `_sanitize_search` (Bug 1: OpenAlex 400s on `?`/`*` in the free-text
`search=` param, which is exactly what natural-language questions contain)
and that `search()` actually sends the sanitized string.
"""

from app import openalex


class FakeResponse:
    def __init__(self, *, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {"results": []}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)


# ---------------------------------------------------------------------------
# _sanitize_search
# ---------------------------------------------------------------------------


def test_sanitize_search_strips_question_mark():
    result = openalex._sanitize_search("What is known about NMC degradation?")
    assert "?" not in result
    assert result == "What is known about NMC degradation"


def test_sanitize_search_strips_asterisk():
    result = openalex._sanitize_search("cathode degrad* mechanisms")
    assert "*" not in result
    assert result == "cathode degrad mechanisms"


def test_sanitize_search_wildcard_heavy_query_not_emptied():
    result = openalex._sanitize_search("What??* is this***?")
    assert "?" not in result
    assert "*" not in result
    assert result.strip() != ""
    assert result == "What is this"


def test_sanitize_search_collapses_whitespace_and_trims():
    result = openalex._sanitize_search("  froth  flotation?   collector  ")
    assert result == "froth flotation collector"


def test_sanitize_search_no_special_chars_is_unchanged():
    result = openalex._sanitize_search("froth flotation collector reagent")
    assert result == "froth flotation collector reagent"


# ---------------------------------------------------------------------------
# search() — sanitized string must be what actually goes in params["search"]
# ---------------------------------------------------------------------------


def test_search_sends_sanitized_query_in_params(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return FakeResponse(json_data={"results": []})

    monkeypatch.setattr(openalex.httpx, "get", fake_get)

    openalex.search("What is known about NMC degradation?", max_results=3)

    assert captured["params"]["search"] == "What is known about NMC degradation"
    assert "?" not in captured["params"]["search"]
