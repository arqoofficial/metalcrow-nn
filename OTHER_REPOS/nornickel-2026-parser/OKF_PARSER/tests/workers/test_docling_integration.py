"""Real Docling conversion against PDFs from SHARED/RAW_DATA.

Docling is mandatory for this project. Tests fail if SHARED/RAW_DATA has no PDFs
or if conversion does not produce substantive markdown.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.models import DoclingConfig
from app.workers.docling import (
    convert_raw_to_markdown,
    docling_version,
    validate_substantive_markdown,
)
from tests.raw_data_samples import discover_raw_data_pdfs

_RAW_DATA_PDFS = discover_raw_data_pdfs(3)


def test_docling_version_is_installed() -> None:
    assert docling_version() not in {"stub", "unknown"}


@pytest.mark.parametrize("pdf_path", _RAW_DATA_PDFS, ids=lambda path: path.name)
def test_docling_extracts_substantive_text_from_raw_data_pdf(pdf_path: Path) -> None:
    config = DoclingConfig(ocr_enabled=False)
    try:
        markdown = convert_raw_to_markdown(pdf_path, docling_config=config)
    except ValueError:
        markdown = convert_raw_to_markdown(
            pdf_path,
            docling_config=DoclingConfig(ocr_enabled=True, ocr_languages=["en", "ru"]),
        )

    validate_substantive_markdown(markdown, pdf_path.name)
    assert "Converted from" not in markdown
    assert "without OCR" not in markdown
