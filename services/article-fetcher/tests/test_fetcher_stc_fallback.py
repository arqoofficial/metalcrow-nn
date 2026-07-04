"""fetch_article tries STC/Nexus only after OpenAlex and all Sci-Hub mirrors
fail, and only when STC is enabled (download_pdf_via_stc handles the gating)."""
from unittest.mock import patch

import pytest

from app import fetcher
from app.fetcher import FetchError, fetch_article


def test_stc_used_when_scihub_exhausted():
    """OpenAlex returns nothing, all mirrors fail -> STC bytes are returned."""
    with (
        patch.object(fetcher, "download_pdf_via_openalex", return_value=None),
        patch.object(fetcher, "download_pdf_via_scidb", return_value=None),
        patch.object(fetcher.settings, "scihub_mirrors", ""),  # no mirrors -> loop empty
        patch.object(fetcher, "download_pdf_via_stc", return_value=b"%PDF-stc") as mock_stc,
    ):
        assert fetch_article("10.1000/x") == b"%PDF-stc"
        mock_stc.assert_called_once_with("10.1000/x")


def test_stc_not_reached_when_openalex_succeeds():
    """OpenAlex hit -> STC never consulted."""
    with (
        patch.object(fetcher, "download_pdf_via_openalex", return_value=b"%PDF-oa"),
        patch.object(fetcher, "download_pdf_via_stc") as mock_stc,
    ):
        assert fetch_article("10.1000/x") == b"%PDF-oa"
        mock_stc.assert_not_called()


def test_scidb_used_before_scihub_and_stc():
    """OpenAlex misses -> SciDB bytes returned before any Sci-Hub mirror or STC."""
    with (
        patch.object(fetcher, "download_pdf_via_openalex", return_value=None),
        patch.object(fetcher, "download_pdf_via_scidb", return_value=b"%PDF-scidb") as mock_scidb,
        patch.object(fetcher, "download_pdf_via_stc") as mock_stc,
    ):
        assert fetch_article("10.1000/x") == b"%PDF-scidb"
        mock_scidb.assert_called_once_with("10.1000/x")
        mock_stc.assert_not_called()


def test_raises_when_all_sources_fail():
    """OpenAlex empty, no mirrors, STC returns None -> FetchError raised."""
    with (
        patch.object(fetcher, "download_pdf_via_openalex", return_value=None),
        patch.object(fetcher, "download_pdf_via_scidb", return_value=None),
        patch.object(fetcher.settings, "scihub_mirrors", ""),
        patch.object(fetcher, "download_pdf_via_stc", return_value=None),
    ):
        with pytest.raises(FetchError):
            fetch_article("10.1000/x")
