"""Anna's Archive SciDB DOI -> PDF source.

When ``settings.scidb_enabled`` is True this resolves a DOI to a downloadable
PDF via Anna's Archive SciDB (``<mirror>/scidb/<bare-doi>``). SciDB aggregates
Sci-Hub + LibGen + Z-Library + Nexus behind a single keyless lookup, so a SciDB
hit is the practical superset of "available on Sci-Hub" — and needs no account
or API key. The ``.gl`` mirror is reachable from this VM (the ``.org``/``.se``
domains are IPv6-only / geo-DNS here).

It is wired into ``fetcher.fetch_article`` AFTER the OpenAlex-OA attempt and
BEFORE the Sci-Hub mirror loop (SciDB is a more reliable superset of Sci-Hub).

Design mirrors ``stc_downloader.py`` and the optional ``curl_cffi`` path:
- Inert: returns ``None`` immediately unless ``settings.scidb_enabled`` is True,
  so when disabled the fetch chain is byte-for-byte unchanged.
- Every failure mode (disabled, SciDB miss, non-PDF bytes, any exception)
  returns ``None``. ``download_pdf_via_scidb`` NEVER raises, so a flaky SciDB
  path can never regress a fetch the legacy chain could serve.
- Both the SciDB page URL AND the page-supplied fast-download PDF URL are passed
  through the shared SSRF guard (``safe_get`` validates the URL and every
  redirect hop via ``assert_public_http_url``) before any bytes are fetched. The
  PDF host is scraped from the SciDB page and is therefore attacker-influenceable.
"""
import logging
import re
from typing import Optional

import requests

from app.config import settings
from app.url_guard import UnsafeUrlError, safe_get

logger = logging.getLogger(__name__)

# Real browser UA — SciDB and the AA fast-download partner hosts gate on it.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
# A direct fast-download PDF link (e.g. https://b4mcx2ml.net/....pdf?...).
_PDF_RE = re.compile(r"https?://[^\s\"'<>]+\.pdf[^\s\"'<>]*")


def _scidb_lookup(doi: str) -> Optional[str]:
    """Return a direct PDF URL from the SciDB page for ``doi``, or None on a miss.

    HIT  = the SciDB page is the paper (``<title>`` is NOT its search page) AND
           the body carries a direct fast-download ``...pdf`` link.
    MISS = the title contains ``- Search -`` (SciDB fell through to its search
           page) or no PDF link is present.

    The SciDB page URL is validated by the SSRF guard before fetch.
    """
    url = "%s/scidb/%s" % (settings.scidb_mirror.rstrip("/"), doi)
    try:
        resp = safe_get(requests.get, url, headers=_HEADERS, timeout=settings.scidb_timeout)
    except UnsafeUrlError:
        logger.warning("SciDB page URL rejected by SSRF guard: %s", url)
        return None
    if resp.status_code != 200:
        logger.info("SciDB lookup for DOI %s returned HTTP %s", doi, resp.status_code)
        return None
    text = resp.text or ""
    title_m = _TITLE_RE.search(text)
    title = title_m.group(1).strip() if title_m else ""
    if "- Search -" in title:
        return None
    pdf_m = _PDF_RE.search(text)
    return pdf_m.group(0) if pdf_m else None


def _download_pdf(pdf_url: str) -> Optional[bytes]:
    """Fetch the page-supplied fast-download URL; return bytes only if a real PDF.

    The PDF host is extracted from the SciDB page and is attacker-influenceable,
    so the URL (and every redirect hop) is validated by the SSRF guard before
    fetch. Bytes are accepted only when they start with the ``%PDF-`` magic.
    """
    try:
        resp = safe_get(requests.get, pdf_url, headers=_HEADERS, timeout=settings.scidb_timeout)
    except UnsafeUrlError:
        logger.warning("SciDB PDF URL rejected by SSRF guard: %s", pdf_url)
        return None
    if resp.status_code != 200:
        return None
    content = resp.content or b""
    if not content.startswith(b"%PDF-"):
        return None
    return content


def download_pdf_via_scidb(doi: str) -> Optional[bytes]:
    """Resolve a DOI to PDF bytes via Anna's Archive SciDB, or None.

    Inert unless ``settings.scidb_enabled`` is True. Never raises.
    """
    if not settings.scidb_enabled:
        return None
    # Imported lazily: fetcher imports this module at top level, so a top-level
    # ``from app.fetcher import _normalize_doi`` would be a circular import.
    from app.fetcher import _normalize_doi

    try:
        bare = _normalize_doi(doi)
        pdf_url = _scidb_lookup(bare)
        if not pdf_url:
            logger.info("SciDB: no downloadable PDF for DOI %s", bare)
            return None
        content = _download_pdf(pdf_url)
    except Exception:
        logger.warning("SciDB path errored for DOI %s; skipping SciDB", doi, exc_info=True)
        return None

    if not content:
        logger.warning("SciDB: PDF link for DOI %s did not yield a valid PDF", bare)
        return None

    logger.info("Fetched %d bytes via Anna's Archive SciDB for DOI %s", len(content), bare)
    return content
