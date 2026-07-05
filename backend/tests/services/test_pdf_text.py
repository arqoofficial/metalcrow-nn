from pathlib import Path

import pytest

from app.services.pdf_text import PdfExtractError, extract_text

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def hello_pdf_bytes() -> bytes:
    return (FIXTURES / "hello.pdf").read_bytes()


def test_extract_text_returns_pdf_content(hello_pdf_bytes: bytes) -> None:
    assert "Metallurgy" in extract_text(hello_pdf_bytes, char_cap=1000)


def test_extract_text_raises_on_unparseable_input() -> None:
    with pytest.raises(PdfExtractError):
        extract_text(b"not a pdf", char_cap=100)


def test_extract_text_truncates_to_char_cap(hello_pdf_bytes: bytes) -> None:
    result = extract_text(hello_pdf_bytes, char_cap=5)
    assert result == "Hello"
    assert len(result) == 5


def test_corrupt_pdf_raises_pdf_extract_error(hello_pdf_bytes: bytes) -> None:
    # Flipping this single byte inside the fixture's `/Resources` dictionary
    # corrupts a NumberObject such that pypdf raises a bare `TypeError`
    # ("'NumberObject' object is not iterable") while parsing pages — not a
    # `pypdf.errors.PdfReadError` and not even a `PyPdfError` subclass at all.
    # This reproduces the exact gap the old `except (PdfReadError, ValueError)`
    # missed: a malformed/untrusted PDF whose failure mode is a raw TypeError.
    # It must still surface as PdfExtractError, never a bare TypeError.
    corrupted = bytearray(hello_pdf_bytes)
    corrupted[287] ^= 0xFF
    with pytest.raises(PdfExtractError):
        extract_text(bytes(corrupted), char_cap=1000)
