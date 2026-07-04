"""Tests for the EuropePMC-by-DOI fulltext fallback.

Gold-OA publishers (MDPI, Hindawi, ...) 403 the fetcher even via curl_cffi and
their DOIs are not on Sci-Hub, but most such papers have a bot-free PMC fulltext.
`europepmc_pdf_url_for_doi` maps DOI -> `europepmc.org/articles/<PMCID>?pdf=render`,
and `_europepmc_or_scihub` inserts that attempt between the direct-URL failure and
the Sci-Hub last resort.
"""
from unittest.mock import MagicMock, patch


def _fake_epmc_resp(result):
    """Build a fake httpx response whose .json() returns a EuropePMC search payload."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    payload = {"resultList": {"result": [result]}} if result is not None else {"resultList": {"result": []}}
    resp.json.return_value = payload
    return resp


# ---------------------------------------------------------------------------
# europepmc_pdf_url_for_doi
# ---------------------------------------------------------------------------

def test_epmc_url_for_oa_pmc_doi():
    """A result with pmcid + inEPMC:Y + hasPDF:Y -> ?pdf=render URL."""
    from app import europepmc

    with patch.object(europepmc.httpx, "get") as mock_get:
        mock_get.return_value = _fake_epmc_resp(
            {"pmcid": "PMC123", "inEPMC": "Y", "hasPDF": "Y"}
        )
        url = europepmc.europepmc_pdf_url_for_doi("10.3390/ijms27073138")

    assert url == "https://europepmc.org/articles/PMC123?pdf=render"


def test_epmc_url_normalizes_doi_url_form():
    """A resolver-URL DOI is normalized to a bare DOI in the EuropePMC query."""
    from app import europepmc

    with patch.object(europepmc.httpx, "get") as mock_get:
        mock_get.return_value = _fake_epmc_resp(
            {"pmcid": "PMC456", "inEPMC": "Y", "hasPDF": "Y"}
        )
        url = europepmc.europepmc_pdf_url_for_doi("https://doi.org/10.1155/2012/848093")

    assert url == "https://europepmc.org/articles/PMC456?pdf=render"
    # Query must use the bare DOI, not the resolver URL.
    params = mock_get.call_args.kwargs["params"]
    assert params["query"] == "DOI:10.1155/2012/848093"


def test_epmc_url_none_when_no_results():
    from app import europepmc

    with patch.object(europepmc.httpx, "get") as mock_get:
        mock_get.return_value = _fake_epmc_resp(None)
        assert europepmc.europepmc_pdf_url_for_doi("10.3390/x") is None


def test_epmc_url_none_when_not_in_epmc():
    """inEPMC:N means no in-EPMC fulltext -> ?pdf=render would 404 -> None."""
    from app import europepmc

    with patch.object(europepmc.httpx, "get") as mock_get:
        mock_get.return_value = _fake_epmc_resp(
            {"pmcid": "PMC789", "inEPMC": "N", "hasPDF": "Y"}
        )
        assert europepmc.europepmc_pdf_url_for_doi("10.3390/x") is None


def test_epmc_url_none_when_no_pmcid():
    from app import europepmc

    with patch.object(europepmc.httpx, "get") as mock_get:
        mock_get.return_value = _fake_epmc_resp({"inEPMC": "Y", "hasPDF": "Y"})
        assert europepmc.europepmc_pdf_url_for_doi("10.3390/x") is None


def test_epmc_url_none_on_transport_error():
    """A network error is best-effort -> None (caller falls through)."""
    from app import europepmc

    with patch.object(europepmc.httpx, "get", side_effect=europepmc.httpx.ConnectError("boom")):
        assert europepmc.europepmc_pdf_url_for_doi("10.3390/x") is None


# ---------------------------------------------------------------------------
# _europepmc_or_scihub
# ---------------------------------------------------------------------------

def test_europepmc_used_before_scihub():
    """EuropePMC URL resolves + downloads -> its bytes returned, fetch_article NOT called."""
    from app import main

    def fake_download(url):
        if "europepmc.org" in url:
            return b"%PDF-epmc"
        raise Exception(f"403 for {url}")

    with (
        patch.object(main, "_download_pdf_from_url", side_effect=fake_download),
        patch.object(main, "europepmc_pdf_url_for_doi",
                     return_value="https://europepmc.org/articles/PMC123?pdf=render"),
        patch.object(main, "fetch_article", side_effect=AssertionError("Sci-Hub must not be reached")) as mock_scihub,
    ):
        out = main._europepmc_or_scihub("job1", "10.3390/ijms27073138")

    assert out == b"%PDF-epmc"
    mock_scihub.assert_not_called()


def test_falls_through_to_scihub_when_no_epmc_url():
    """No EuropePMC fulltext -> fetch_article(doi) is the fallback."""
    from app import main

    with (
        patch.object(main, "_download_pdf_from_url") as mock_dl,
        patch.object(main, "europepmc_pdf_url_for_doi", return_value=None),
        patch.object(main, "fetch_article", return_value=b"%PDF-scihub") as mock_scihub,
    ):
        out = main._europepmc_or_scihub("job2", "10.1/x")

    assert out == b"%PDF-scihub"
    mock_dl.assert_not_called()
    mock_scihub.assert_called_once_with("10.1/x")


def test_falls_through_to_scihub_when_epmc_download_fails():
    """EuropePMC URL resolves but its download errors -> fetch_article fallback."""
    from app import main

    with (
        patch.object(main, "_download_pdf_from_url", side_effect=Exception("epmc 500")),
        patch.object(main, "europepmc_pdf_url_for_doi",
                     return_value="https://europepmc.org/articles/PMC123?pdf=render"),
        patch.object(main, "fetch_article", return_value=b"%PDF-scihub") as mock_scihub,
    ):
        out = main._europepmc_or_scihub("job3", "10.3390/x")

    assert out == b"%PDF-scihub"
    mock_scihub.assert_called_once_with("10.3390/x")
