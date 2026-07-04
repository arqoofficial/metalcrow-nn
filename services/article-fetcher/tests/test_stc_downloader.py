"""Unit tests for the STC/Nexus IPFS downloader. No real IPFS or libstc-geck
is required: the optional dependency is imported lazily inside the function,
so these tests exercise the gating and the parse/validate logic via patches.
"""
from unittest.mock import patch

from app import stc_downloader


def test_disabled_returns_none_without_importing():
    """stc_enabled=False -> returns None immediately, never touches the dep."""
    with patch.object(stc_downloader.settings, "stc_enabled", False):
        assert stc_downloader.download_pdf_via_stc("10.1/x") is None


def test_extract_pdf_cid_picks_pdf_link():
    links = [
        {"cid": "bafyA", "extension": "txt", "filename": "a.txt"},
        {"cid": "bafyPDF", "extension": "pdf", "filename": "paper.pdf"},
    ]
    assert stc_downloader._extract_pdf_cid(links) == "bafyPDF"


def test_extract_pdf_cid_none_when_no_pdf():
    assert stc_downloader._extract_pdf_cid([{"cid": "x", "extension": "txt"}]) is None
    assert stc_downloader._extract_pdf_cid([]) is None
    assert stc_downloader._extract_pdf_cid(None) is None


def test_enabled_but_dep_missing_returns_none():
    """stc_enabled=True but libstc-geck not installed -> None (no crash)."""
    with (
        patch.object(stc_downloader.settings, "stc_enabled", True),
        patch.object(stc_downloader, "_run_stc_query", side_effect=ImportError("no stc_geck")),
    ):
        assert stc_downloader.download_pdf_via_stc("10.1/x") is None


def test_enabled_success_path_validates_pdf():
    """Happy path: query yields a pdf CID, gateway returns valid PDF bytes."""
    with (
        patch.object(stc_downloader.settings, "stc_enabled", True),
        patch.object(stc_downloader, "_run_stc_query", return_value="bafyPDF"),
        patch.object(stc_downloader, "_fetch_ipfs_bytes", return_value=b"%PDF-1.5 stc"),
    ):
        assert stc_downloader.download_pdf_via_stc("10.1/x") == b"%PDF-1.5 stc"


def test_enabled_non_pdf_bytes_returns_none():
    with (
        patch.object(stc_downloader.settings, "stc_enabled", True),
        patch.object(stc_downloader, "_run_stc_query", return_value="bafyPDF"),
        patch.object(stc_downloader, "_fetch_ipfs_bytes", return_value=b"<html>nope"),
    ):
        assert stc_downloader.download_pdf_via_stc("10.1/x") is None


def test_enabled_no_cid_returns_none():
    with (
        patch.object(stc_downloader.settings, "stc_enabled", True),
        patch.object(stc_downloader, "_run_stc_query", return_value=None),
    ):
        assert stc_downloader.download_pdf_via_stc("10.1/x") is None


def test_query_error_returns_none():
    """Any error inside the query path degrades to None, never raises."""
    with (
        patch.object(stc_downloader.settings, "stc_enabled", True),
        patch.object(stc_downloader, "_run_stc_query", side_effect=RuntimeError("boom")),
    ):
        assert stc_downloader.download_pdf_via_stc("10.1/x") is None


def test_first_document_plain_list():
    """A plain list of docs -> first element."""
    doc = {"links": [{"cid": "x", "extension": "pdf"}]}
    assert stc_downloader._first_document([doc, {"other": 1}]) == doc


def test_first_document_documents_dict():
    """A dict with 'documents' -> first of that list."""
    doc = {"links": []}
    assert stc_downloader._first_document({"documents": [doc, {"y": 2}]}) == doc


def test_first_document_collector_outputs():
    """A dict with 'collector_outputs' -> collector_output.documents[0]."""
    doc = {"links": [{"cid": "c", "extension": "pdf"}]}
    result = {
        "collector_outputs": [
            {"collector_output": {"documents": [doc]}},
        ]
    }
    assert stc_downloader._first_document(result) == doc


def test_first_document_unwraps_document_wrapper():
    """A scored doc wrapping its payload under 'document' is unwrapped."""
    inner = {"links": [{"cid": "z", "extension": "pdf"}]}
    assert stc_downloader._first_document([{"document": inner, "score": 1.0}]) == inner


def test_first_document_unrecognized_shapes_return_none():
    """Shapes recognized as empty / unexpected -> None."""
    assert stc_downloader._first_document(None) is None
    assert stc_downloader._first_document(42) is None
    assert stc_downloader._first_document({}) is None
    assert stc_downloader._first_document([]) is None


def test_enabled_ipfs_fetch_raises_returns_none():
    """Query yields a CID but IPFS fetch raises -> None (no raise)."""
    with (
        patch.object(stc_downloader.settings, "stc_enabled", True),
        patch.object(stc_downloader, "_run_stc_query", return_value="bafyPDF"),
        patch.object(
            stc_downloader,
            "_fetch_ipfs_bytes",
            side_effect=RuntimeError("gateway down"),
        ),
    ):
        assert stc_downloader.download_pdf_via_stc("10.1/x") is None
