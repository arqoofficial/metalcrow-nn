"""Unit tests for the Anna's Archive SciDB downloader.

No real network is touched: ``requests.get`` is mocked and the SSRF guard
(``app.url_guard.assert_public_http_url``, which ``safe_get`` calls internally)
is stubbed to a no-op so no DNS resolution happens.
"""
from unittest.mock import MagicMock, patch

from app import scidb_downloader

# A page whose <title> is the paper (a HIT) carrying a direct fast-download link.
_HIT_PAGE = (
    "<html><head><title>A Real Paper Title — Anna's Archive</title></head>"
    "<body><a href=\"https://b4mcx2ml.net/d/abc123/paper.pdf?key=x\">download</a>"
    "</body></html>"
)
# A page that fell through to SciDB's search view (a MISS).
_SEARCH_PAGE = (
    "<html><head><title>scidb - Search - Anna's Archive</title></head>"
    "<body>no direct result</body></html>"
)


def _resp(status=200, text="", content=b""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.content = content
    r.headers = {}  # so safe_get's _is_redirect sees no Location
    return r


def test_hit_returns_pdf_bytes():
    """Hit page (paper title + .pdf link) then a %PDF body -> returns bytes."""
    pdf_bytes = b"%PDF-1.7 real pdf body"
    with (
        patch.object(scidb_downloader.settings, "scidb_enabled", True),
        patch("app.url_guard.assert_public_http_url", return_value=None),
        patch.object(
            scidb_downloader.requests,
            "get",
            side_effect=[_resp(text=_HIT_PAGE), _resp(content=pdf_bytes)],
        ) as mock_get,
    ):
        assert scidb_downloader.download_pdf_via_scidb("10.1000/x") == pdf_bytes
        assert mock_get.call_count == 2


def test_search_page_returns_none():
    """A '- Search -' page is a miss -> None, and the PDF fetch never fires."""
    with (
        patch.object(scidb_downloader.settings, "scidb_enabled", True),
        patch("app.url_guard.assert_public_http_url", return_value=None),
        patch.object(
            scidb_downloader.requests, "get", side_effect=[_resp(text=_SEARCH_PAGE)]
        ) as mock_get,
    ):
        assert scidb_downloader.download_pdf_via_scidb("10.1000/x") is None
        assert mock_get.call_count == 1  # only the lookup, no PDF download


def test_hit_but_non_pdf_body_returns_none():
    """Hit page but the PDF link yields a non-%PDF body -> None."""
    with (
        patch.object(scidb_downloader.settings, "scidb_enabled", True),
        patch("app.url_guard.assert_public_http_url", return_value=None),
        patch.object(
            scidb_downloader.requests,
            "get",
            side_effect=[_resp(text=_HIT_PAGE), _resp(content=b"<html>not a pdf")],
        ),
    ):
        assert scidb_downloader.download_pdf_via_scidb("10.1000/x") is None


def test_disabled_returns_none_without_network():
    """scidb_enabled=False -> None immediately, no network call."""
    with (
        patch.object(scidb_downloader.settings, "scidb_enabled", False),
        patch.object(scidb_downloader.requests, "get") as mock_get,
    ):
        assert scidb_downloader.download_pdf_via_scidb("10.1000/x") is None
        mock_get.assert_not_called()


def test_network_exception_returns_none():
    """Any network error degrades to None, never raises."""
    with (
        patch.object(scidb_downloader.settings, "scidb_enabled", True),
        patch("app.url_guard.assert_public_http_url", return_value=None),
        patch.object(
            scidb_downloader.requests, "get", side_effect=ConnectionError("boom")
        ),
    ):
        assert scidb_downloader.download_pdf_via_scidb("10.1000/x") is None
