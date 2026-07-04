"""Resolve a bot-free EuropePMC fulltext PDF URL for a DOI.

Gold-OA publishers (MDPI, Hindawi, Frontiers, ...) 403 the fetcher even via
curl_cffi, and Sci-Hub does not mirror gold-OA. But most such papers also have a
PMC copy that EuropePMC serves with no bot wall at
``europepmc.org/articles/<PMCID>?pdf=render``. This module maps DOI -> that URL.
"""
import logging

import httpx

from app.url_guard import UnsafeUrlError, assert_public_http_url

logger = logging.getLogger(__name__)

_EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def europepmc_pdf_url_for_doi(doi: str) -> str | None:
    """Return a EuropePMC ``?pdf=render`` URL if the DOI has an OA PMC fulltext, else None.

    Best-effort: any network/parse error or a paper without a PMC fulltext returns None,
    so the caller falls through to its next fallback (never raises).
    """
    from app.fetcher import _normalize_doi  # reuse the URL->bare-DOI normalizer

    bare = _normalize_doi(doi)
    try:
        resp = httpx.get(
            _EPMC_SEARCH,
            params={"query": f"DOI:{bare}", "format": "json", "resultType": "lite", "pageSize": 1},
            timeout=8.0,
            headers={"User-Agent": "cosmetica-article-fetcher/1.0"},
        )
        resp.raise_for_status()
        results = (resp.json().get("resultList") or {}).get("result") or []
    except Exception:
        logger.warning("EuropePMC lookup failed for DOI %s", bare, exc_info=True)
        return None
    if not results:
        return None
    rec = results[0]
    pmcid = rec.get("pmcid")
    # Require an actual in-EPMC OA fulltext, else ?pdf=render 404s.
    if not pmcid or rec.get("inEPMC") != "Y" or rec.get("hasPDF") != "Y":
        return None
    url = f"https://europepmc.org/articles/{pmcid}?pdf=render"
    try:
        assert_public_http_url(url)
    except UnsafeUrlError:
        return None
    return url
