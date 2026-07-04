import logging
import re
import subprocess

from app.config import settings
from app.openalex_downloader import download_pdf_via_openalex
from app.scidb_downloader import download_pdf_via_scidb
from app.stc_downloader import download_pdf_via_stc
from app.url_guard import UnsafeUrlError, assert_public_http_url

logger = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
# `--max-redirs 10` bounds redirect chasing; the URL is validated up-front by
# assert_public_http_url. NOTE: curl's own intermediate redirect hops are not
# individually re-validated against the SSRF denylist (residual risk, scoped to
# the Sci-Hub / paywalled-mirror path only). Cap raised 5->10 because legit
# publishers (e.g. Springer) use longer redirect chains.
_CURL_BASE = ["curl", "-L", "--max-redirs", "10", "-A", _USER_AGENT, "--max-time", "30", "-s", "-f"]

# A DOI must look like 10.NNNN/suffix — reject anything else before building a
# Sci-Hub mirror URL (stops a crafted "DOI" from injecting a path/host).
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")

# Known wrappers around a bare DOI. Callers (and upstream APIs) routinely pass a
# resolver URL (https://doi.org/10...) or a `doi:` prefix instead of the bare
# `10.NNNN/...` form; strip these before validation so the Sci-Hub fallback runs.
_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)


def _normalize_doi(raw: str) -> str:
    """Strip common DOI URL/prefix wrappers, returning a bare DOI.

    Case-insensitive on the scheme/host prefix, but slices from the ORIGINAL
    string so the DOI's own case is preserved (DOIs are case-insensitive but we
    don't mangle them). A non-DOI string is returned unchanged so downstream
    validation still rejects it.
    """
    doi = raw.strip()
    lowered = doi.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            return doi[len(prefix):]
    return doi


class FetchError(Exception):
    pass


def _curl_get_bytes(url: str) -> bytes:
    """Fetch URL bytes via curl subprocess."""
    # SSRF guard: reject private/loopback/link-local/reserved targets up front.
    try:
        assert_public_http_url(url)
    except UnsafeUrlError as exc:
        raise FetchError(f"Blocked unsafe URL {url}: {exc}") from exc
    # `--` terminates curl option parsing so a url starting with `-` is never
    # interpreted as a curl flag (option-injection hardening).
    result = subprocess.run(
        [*_CURL_BASE, "--", url],
        capture_output=True,
        timeout=35,
    )
    if result.returncode != 0:
        raise FetchError("curl failed (rc=%d): %s" % (result.returncode, result.stderr.decode(errors="replace")[:200]))
    return result.stdout


def _extract_pdf_url(html: str, mirror: str) -> str:
    """Extract PDF URL from a sci-hub page.

    Handles two known layouts:
    - <meta name="citation_pdf_url" content="..."> (older layout)
    - <iframe src="//...pdf..."> (newer layout, PDF served from CDN)
    """
    # Layout 1: citation_pdf_url meta tag (both attribute orders)
    match = re.search(r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']', html)
    if not match:
        match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']', html)

    # Layout 2: iframe whose src ends with .pdf (strip fragment); allow spaces around =
    if not match:
        match = re.search(r'<iframe[^>]+src\s*=\s*["\']([^"\']+\.pdf[^"\']*)["\']', html, re.IGNORECASE)

    if not match:
        return None

    pdf_path = match.group(1).split("#")[0]  # strip #view=FitH etc.
    if pdf_path.startswith("//"):
        return "https:" + pdf_path
    if pdf_path.startswith("/"):
        return mirror + pdf_path
    return pdf_path


def fetch_article(doi: str) -> bytes:
    """Download a PDF for the given DOI.

    Tries OpenAlex (managed content + open-access pdf_url) first, then falls
    back to the Sci-Hub mirror loop. OpenAlex failures degrade gracefully to
    Sci-Hub — they never break the fallback path.
    """
    doi = _normalize_doi(doi)
    if not _DOI_RE.match(doi):
        raise FetchError(f"Invalid DOI: {doi!r}")

    try:
        pdf = download_pdf_via_openalex(doi)
        if pdf:
            logger.info("Fetched %d bytes for DOI %s via OpenAlex", len(pdf), doi)
            return pdf
    except Exception:
        logger.warning(
            "OpenAlex download path errored for DOI %s; falling back to Sci-Hub", doi, exc_info=True
        )

    # Anna's Archive SciDB (keyless superset of Sci-Hub). Inert unless
    # SCIDB_ENABLED; download_pdf_via_scidb handles the gating and never raises.
    # Tried after OpenAlex-OA and before the Sci-Hub mirror loop, so it never
    # regresses a fetch the legacy chain could serve.
    try:
        scidb_pdf = download_pdf_via_scidb(doi)
    except Exception:
        # download_pdf_via_scidb is contractually non-raising, but stay defensive.
        logger.warning("SciDB fallback errored for DOI %s", doi, exc_info=True)
        scidb_pdf = None
    if scidb_pdf:
        logger.info("Fetched %d bytes via Anna's Archive SciDB for DOI %s", len(scidb_pdf), doi)
        return scidb_pdf

    last_error: Exception = FetchError("No mirrors configured")

    for mirror in settings.scihub_mirror_list:
        logger.info("Trying mirror %s for DOI %s", mirror, doi)
        try:
            page_bytes = _curl_get_bytes("%s/%s" % (mirror, doi))
        except FetchError as e:
            logger.warning("Mirror %s unreachable for DOI %s: %s", mirror, doi, e)
            last_error = e
            continue
        except Exception:
            logger.warning("Mirror %s failed for DOI %s", mirror, doi, exc_info=True)
            last_error = FetchError("Failed to reach %s" % mirror)
            continue

        html = page_bytes.decode("utf-8", errors="replace")
        pdf_url = _extract_pdf_url(html, mirror)

        if not pdf_url:
            logger.warning("No PDF URL found on mirror %s for DOI %s", mirror, doi)
            last_error = FetchError("Article not available on sci-hub")
            continue

        logger.info("Found PDF URL for DOI %s: %s", doi, pdf_url)

        try:
            pdf_bytes = _curl_get_bytes(pdf_url)
        except FetchError as e:
            logger.warning("PDF download failed from %s for DOI %s: %s", pdf_url, doi, e)
            last_error = e
            continue
        except Exception:
            logger.warning("PDF download failed from %s for DOI %s", pdf_url, doi, exc_info=True)
            last_error = FetchError("Failed to download PDF")
            continue

        if len(pdf_bytes) == 0 or not pdf_bytes.startswith(b"%PDF"):
            logger.warning("Invalid PDF from %s for DOI %s", mirror, doi)
            last_error = FetchError("Downloaded file is not a valid PDF")
            continue

        logger.info("Fetched %d bytes for DOI %s via %s", len(pdf_bytes), doi, mirror)
        return pdf_bytes

    # Final fallback: STC / Nexus over IPFS (inert unless STC_ENABLED). Tried
    # only after OpenAlex OA and every Sci-Hub mirror failed — never regresses
    # a fetch the legacy chain could serve.
    try:
        stc_pdf = download_pdf_via_stc(doi)
    except Exception:
        # download_pdf_via_stc is contractually non-raising, but stay defensive.
        logger.warning("STC fallback errored for DOI %s", doi, exc_info=True)
        stc_pdf = None
    if stc_pdf:
        return stc_pdf

    raise last_error
