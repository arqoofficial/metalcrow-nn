"""PDF text and table extraction via pdfplumber."""

import io
import logging
import re
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)


def extract_text_from_pdf(source: bytes | Path) -> tuple[str, int]:
    """
    Extract text from a PDF file (bytes or path).

    Also extracts tables and converts them to readable text so that
    tabular data on heat treatment regimes is not lost.

    Returns (full_text, page_count).
    """
    if isinstance(source, Path):
        source = source.read_bytes()

    pages_text: list[str] = []

    with pdfplumber.open(io.BytesIO(source)) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            parts: list[str] = []

            # Main text block
            text = page.extract_text(x_tolerance=2, y_tolerance=2)
            if text:
                parts.append(text)

            # Tables — convert to sentence-like text for NLP
            for table in page.extract_tables():
                table_text = _table_to_text(table)
                if table_text:
                    parts.append(table_text)

            if parts:
                pages_text.append("\n".join(parts))

    raw = "\n\n".join(pages_text)
    return clean_extracted_text(raw), page_count


def _table_to_text(table: list[list[str | None]]) -> str:
    """
    Convert a pdfplumber table (list of rows) to prose sentences.

    Example row: ["820°C / 2h", "1404 MPa", "11%"]
    becomes: "820°C / 2h: tensile strength 1404 MPa elongation 11%"

    We emit header+value pairs so the NLP pipeline can pick up
    regime → property relations from tabular data.
    """
    if not table or len(table) < 2:
        return ""

    rows = [[cell or "" for cell in row] for row in table]
    header = rows[0]
    lines: list[str] = []

    for row in rows[1:]:
        pairs = [f"{h} {v}" for h, v in zip(header, row) if h.strip() and v.strip()]
        if pairs:
            lines.append(". ".join(pairs) + ".")

    return " ".join(lines)


def clean_extracted_text(raw: str) -> str:
    """
    Post-process raw PDF text:
    - Remove soft hyphens and hyphenation at line breaks (word-\\nbreak → wordbreak)
    - Collapse multiple whitespace
    - Remove non-printable characters
    """
    # Dehyphenation: "proper-\nty" → "property"
    text = re.sub(r"-\s*\n\s*", "", raw)
    # Remove non-printable except newlines and tabs
    text = re.sub(r"[^\x09\x0A\x20-\x7E\u0400-\u04FF\u00C0-\u024F°²³×µ]", " ", text)
    # Collapse runs of spaces (but keep newlines)
    text = re.sub(r"[ \t]+", " ", text)
    # Collapse 3+ newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
