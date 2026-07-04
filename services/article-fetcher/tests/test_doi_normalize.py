"""Tests for DOI normalization (app/fetcher.py _normalize_doi).

Pure-Python, no network. Covers URL/`doi:` wrapper stripping and the guard that
URL-form DOIs now get PAST validation (the Sci-Hub fallback was 100% broken
because a full resolver URL was passed to the bare-DOI validator).
"""
import pytest

from app.fetcher import _normalize_doi, fetch_article

_BARE = "10.1155/2015/590470"


@pytest.mark.parametrize(
    "raw",
    [
        "https://doi.org/10.1155/2015/590470",
        "http://doi.org/10.1155/2015/590470",
        "https://dx.doi.org/10.1155/2015/590470",
        "http://dx.doi.org/10.1155/2015/590470",
        "doi:10.1155/2015/590470",
        "  https://doi.org/10.1155/2015/590470  ",
        "\thttps://doi.org/10.1155/2015/590470\n",
    ],
)
def test_normalize_strips_wrappers(raw):
    assert _normalize_doi(raw) == _BARE


def test_normalize_bare_doi_unchanged():
    assert _normalize_doi(_BARE) == _BARE


def test_normalize_scheme_host_case_insensitive():
    # Scheme/host matched case-insensitively; DOI body case preserved.
    assert _normalize_doi("HTTPS://DOI.ORG/10.1155/2015/590470") == _BARE


def test_normalize_preserves_doi_case():
    # DOI suffixes can contain letters whose case must be preserved.
    assert _normalize_doi("https://doi.org/10.1234/AbC.DeF") == "10.1234/AbC.DeF"


def test_normalize_non_doi_returned_as_is():
    assert _normalize_doi("garbage") == "garbage"


def test_fetch_article_url_form_doi_passes_validation(monkeypatch):
    """A URL-form DOI must get PAST the validation guard (no Invalid DOI raise)."""
    monkeypatch.setattr(
        "app.fetcher.download_pdf_via_openalex",
        lambda doi: b"%PDF-1.4 ok",
    )
    result = fetch_article("https://doi.org/10.1155/2015/590470")
    assert result == b"%PDF-1.4 ok"
