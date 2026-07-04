"""Shared PDF-response validation.

Extracted into its own tiny module so both ``app.main`` (the plain/curl_cffi
fetch chain) and ``app.headless_downloader`` (the stealth-browser tier) can
validate a fetched payload as a real PDF without importing each other — a
direct ``from app.main import _validate_pdf`` would create a circular import
because ``main`` imports the headless downloader for the fetch chain.
"""
from app.fetcher import FetchError

__all__ = ["validate_pdf"]


def validate_pdf(url: str, status_code: int, content: bytes, content_type: str) -> bytes:
    """Shared validation: HTTP 200 + PDF-ish payload, else FetchError.

    A payload is accepted only when the response was HTTP 200 AND the body either
    starts with the ``%PDF`` magic bytes or the ``Content-Type`` says
    ``application/pdf``. An Akamai/Cloudflare JS challenge page returns HTTP 200
    with an HTML body, so this correctly rejects it.
    """
    if status_code != 200:
        raise FetchError(f"Direct URL fetch returned HTTP {status_code} for {url}")
    content = content or b""
    ct = (content_type or "").lower()
    if not content or not (content.startswith(b"%PDF") or "application/pdf" in ct):
        raise FetchError(f"Direct URL did not yield a valid PDF for {url}")
    return content
