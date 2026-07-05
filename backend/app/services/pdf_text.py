"""Light full-text extraction from PDF bytes via `pypdf` — no OCR, no layout
analysis, just per-page `extract_text()` joined together. Used by the Celery
worker (litsearch → chat integration, see design doc §8) to turn a fetched
PDF's raw bytes into dialog-available full text; truncated to a caller-chosen
`char_cap` before storage/use, since LLM context is finite.
"""

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PyPdfError

logger = logging.getLogger(__name__)


class PdfExtractError(Exception):
    """Raised when `pdf_bytes` cannot be parsed as a PDF."""


def extract_text(pdf_bytes: bytes, *, char_cap: int) -> str:
    """Extract text from all pages of `pdf_bytes`, joined in page order, then
    truncated to `char_cap` characters. Raises `PdfExtractError` if the input
    cannot be parsed as a PDF."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "".join(page.extract_text() or "" for page in reader.pages)
    except (PyPdfError, TypeError, ValueError) as exc:
        logger.warning("pdf_text extraction failed: %s", exc)
        raise PdfExtractError(str(exc)) from exc
    # PostgreSQL text columns reject NUL (0x00) bytes, which pypdf can emit from
    # malformed or embedded-font PDFs. Strip them so persisting `fulltext_text`
    # can never crash the worker with `psycopg.DataError: ... NUL (0x00) bytes`.
    text = text.replace("\x00", "")
    return text[:char_cap]
