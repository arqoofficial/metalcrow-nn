"""OpenAlex-based PDF downloader.

Preferred fetch path ahead of the Sci-Hub scraper. Acquisition priority:

1. Managed content download (paid, ~$0.01, free tier 100/day, needs api_key):
   ``has_content.pdf == true`` -> GET ``content_url`` with api_key.
2. Open-access pdf_url (free): from location objects, in order
   ``best_oa_location.pdf_url``, ``primary_location.pdf_url`` (if is_oa),
   any ``locations[].pdf_url`` where is_oa, finally ``open_access.oa_url``.
3. (Sci-Hub fallback lives in fetcher.py, not here.)

All failures degrade to ``None`` — this module never raises into ``fetch_article``.
"""

import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import httpx
import redis as redis_lib

from app.config import settings
from app.url_guard import safe_get

logger = logging.getLogger(__name__)

# Fields we actually need; keeps the work payload small.
# NOTE: ``content_url`` was REMOVED — OpenAlex no longer accepts it as a select
# field and returns HTTP 400 for the *entire* call when it is requested. That
# broke the whole OA path (every DOI fell through to Sci-Hub). ``has_content``
# is still a valid 200 field and is kept; the managed-content branch below now
# always sees ``content_url == None`` and skips (the paid managed-content path
# is dead upstream anyway).
_SELECT_FIELDS = (
    "ids,doi,has_content,best_oa_location,"
    "primary_location,locations,open_access"
)
_HTTP_TIMEOUT = 30.0

# Browser-like UA to reduce anti-bot blocks on OA publisher hosts.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Module-level redis handle (lazy); _get_redis is patched in tests.
_redis_client: Optional[redis_lib.Redis] = None


class OpenAlexUnavailable(Exception):
    """Internal sentinel — OpenAlex could not provide a PDF."""


def _get_redis() -> Optional[redis_lib.Redis]:
    """Return a shared Redis client, or None if it cannot be created."""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis_lib.from_url(settings.redis_url, decode_responses=True)
        except Exception:
            logger.warning("OpenAlex: could not create Redis client", exc_info=True)
            return None
    return _redis_client


def _looks_like_pdf(content_type: Optional[str], body: bytes) -> bool:
    """True iff body is a non-empty PDF (magic bytes or content-type), not HTML."""
    if not body:
        return False
    head = body[:1024].lstrip()
    lowered = head[:64].lower()
    if lowered.startswith(b"<!doctype") or lowered.startswith(b"<html"):
        return False
    if body.startswith(b"%PDF"):
        return True
    if content_type and "application/pdf" in content_type.lower():
        return True
    return False


def _http_get_json(url: str) -> Optional[dict]:
    """GET JSON, returning the parsed dict or None on any error/non-2xx."""
    try:
        resp = httpx.get(url, timeout=_HTTP_TIMEOUT, follow_redirects=True)
    except Exception:
        logger.warning("OpenAlex: JSON GET failed for %s", url, exc_info=True)
        return None
    if resp.status_code >= 400:
        logger.warning("OpenAlex: JSON GET %s returned %d", url, resp.status_code)
        return None
    try:
        return resp.json()
    except Exception:
        logger.warning("OpenAlex: could not parse JSON from %s", url, exc_info=True)
        return None


def _http_get_bytes(url: str, headers: Optional[dict] = None):
    """GET raw bytes. Returns an httpx.Response (has .content, .headers, .status_code).

    SSRF guard: validates the URL + every redirect hop before fetching. An
    UnsafeUrlError propagates to the caller (``_try_download`` catches Exception
    and returns None — a blocked internal URL safely becomes "no PDF").
    """
    return safe_get(
        lambda u, **kw: httpx.get(u, timeout=_HTTP_TIMEOUT, **kw),
        url,
        headers=headers or {},
        redirect_kwarg="follow_redirects",
    )


def _browser_headers(url: str) -> dict:
    """Browser-like headers for an OA publisher PDF GET (reduces anti-bot 4xx)."""
    parts = urllib.parse.urlsplit(url)
    referer = f"{parts.scheme}://{parts.netloc}/" if parts.scheme and parts.netloc else url
    return {
        "User-Agent": _USER_AGENT,
        "Accept": "application/pdf,*/*",
        "Referer": referer,
    }


def _append_query(url: str, params: dict) -> str:
    """Append query params to a URL, preserving any existing query string."""
    parts = urllib.parse.urlsplit(url)
    existing = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    merged = existing + [(k, v) for k, v in params.items() if v]
    new_query = urllib.parse.urlencode(merged)
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
    )


def _fetch_work(doi: str) -> Optional[dict]:
    """GET the OpenAlex work JSON by DOI. Returns the work dict, or None on any error.

    Iterates the article-fetcher's ordered OpenAlex key list (key2 preferred) and
    fails over to the next key whenever a lookup yields no JSON — which covers the
    auth/rate-limit/quota (401/403/429) and transient 5xx cases, since
    ``_http_get_json`` already collapses every non-2xx to ``None``. Returns the
    first valid work JSON, else ``None`` (so ``fetch_article`` falls back to
    Sci-Hub). With a single configured (or no) key this behaves exactly as before.
    """
    base = settings.openalex_api_base.rstrip("/")
    # OpenAlex accepts a bare DOI or the full https://doi.org/ form after `doi:`.
    doi_clean = doi.strip()
    work_path = f"{base}/works/doi:{urllib.parse.quote(doi_clean, safe='/:')}"

    api_keys = settings.openalex_api_keys
    # No key configured: single anonymous attempt (matches prior behavior).
    key_candidates: list[Optional[str]] = api_keys if api_keys else [None]

    for idx, api_key in enumerate(key_candidates):
        params = {"select": _SELECT_FIELDS}
        if api_key:
            params["api_key"] = api_key
        if settings.openalex_mailto:
            params["mailto"] = settings.openalex_mailto
        url = _append_query(work_path, params)
        work = _http_get_json(url)
        if work:
            return work
        if idx + 1 < len(key_candidates):
            logger.warning(
                "OpenAlex: work lookup failed for DOI %s on key #%d; failing over to next key",
                doi, idx + 1,
            )
    return None


def _content_cap_exceeded() -> bool:
    """True if today's managed-content download count is at/above the cap.

    Defensive: if Redis is unavailable we log a WARNING and treat the cap as
    NOT exceeded — blocking would break fetching, which is worse than risking
    a small overage on the (paid) managed-content path.
    """
    client = _get_redis()
    if client is None:
        logger.warning("OpenAlex: Redis unavailable, cannot enforce content cap; allowing")
        return False
    key = _content_counter_key()
    try:
        raw = client.get(key)
    except Exception:
        logger.warning("OpenAlex: Redis GET failed for content cap; allowing", exc_info=True)
        return False
    try:
        current = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        current = 0
    return current >= settings.openalex_content_daily_cap


def _content_counter_key() -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"openalex_content_downloads:{day}"


def _record_content_download() -> None:
    """INCR the daily managed-content counter (called only on success)."""
    client = _get_redis()
    if client is None:
        return
    key = _content_counter_key()
    try:
        client.incr(key)
        client.expire(key, 2 * 24 * 3600)  # ~2 days
    except Exception:
        logger.warning("OpenAlex: failed to record content download count", exc_info=True)


def _oa_pdf_candidates(work: dict) -> list:
    """Ordered list of (label, url) free OA pdf candidates."""
    out = []

    best = work.get("best_oa_location") or {}
    if best.get("pdf_url"):
        out.append(("OA pdf_url (best_oa_location)", best["pdf_url"]))

    primary = work.get("primary_location") or {}
    if primary.get("is_oa") and primary.get("pdf_url"):
        out.append(("OA pdf_url (primary_location)", primary["pdf_url"]))

    for loc in work.get("locations") or []:
        if loc and loc.get("is_oa") and loc.get("pdf_url"):
            out.append(("OA pdf_url (locations[])", loc["pdf_url"]))

    oa = work.get("open_access") or {}
    if oa.get("oa_url"):
        out.append(("OA oa_url (open_access)", oa["oa_url"]))

    # De-dup by URL, preserve order.
    seen = set()
    deduped = []
    for label, url in out:
        if url not in seen:
            seen.add(url)
            deduped.append((label, url))
    return deduped


def _try_download(label: str, url: str, needs_api_key: bool) -> Optional[bytes]:
    """GET a single candidate URL; return PDF bytes if valid, else None."""
    final_url = url
    if needs_api_key and settings.openalex_api_key:
        final_url = _append_query(url, {"api_key": settings.openalex_api_key})
    headers = _browser_headers(final_url)
    try:
        resp = _http_get_bytes(final_url, headers)
    except Exception:
        logger.warning("OpenAlex: GET failed for %s (%s)", label, url, exc_info=True)
        return None
    status = getattr(resp, "status_code", 0)
    if status >= 400:
        logger.warning("OpenAlex: %s returned %d for %s", label, status, url)
        return None
    body = resp.content or b""
    content_type = (resp.headers or {}).get("content-type")
    if not _looks_like_pdf(content_type, body):
        logger.info("OpenAlex: %s did not yield a PDF (likely landing page) for %s", label, url)
        return None
    return body


def download_pdf_via_openalex(doi: str) -> Optional[bytes]:
    """Try to download a PDF for ``doi`` via OpenAlex. Returns bytes or None."""
    if not settings.openalex_api_base:
        return None

    work = _fetch_work(doi)
    if not work:
        logger.info("OpenAlex: no work found for DOI %s", doi)
        return None

    # 1. Managed content (preferred) — gated by api_key + daily cap.
    has_content = work.get("has_content") or {}
    content_url = work.get("content_url")
    if content_url and (has_content.get("pdf") or has_content.get("grobid_xml")):
        if not settings.openalex_api_key:
            logger.info("OpenAlex: managed content available for %s but no api_key; skipping", doi)
        elif _content_cap_exceeded():
            logger.info("OpenAlex: managed-content daily cap reached; skipping for %s", doi)
        else:
            pdf = _try_download("OpenAlex managed content", content_url, needs_api_key=True)
            if pdf:
                _record_content_download()
                logger.info("OpenAlex: fetched %d bytes for %s via managed content", len(pdf), doi)
                return pdf

    # 2. Open-access pdf_url candidates (free, no api_key).
    for label, url in _oa_pdf_candidates(work):
        pdf = _try_download(label, url, needs_api_key=False)
        if pdf:
            logger.info("OpenAlex: fetched %d bytes for %s via %s", len(pdf), doi, label)
            return pdf

    logger.info("OpenAlex: no downloadable PDF for DOI %s", doi)
    return None
