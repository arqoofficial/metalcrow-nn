"""Tests for PDF extraction and the /documents/pdf endpoint."""

import io
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from fastapi import FastAPI
from science_kg.api.routes import router
from science_kg.nlp.pipeline import build_pipeline
from science_kg.nlp.pdf_extractor import clean_extracted_text, _table_to_text


# ── Unit tests for pdf_extractor helpers ─────────────────────────────────────


class TestCleanExtractedText:
    def test_dehyphenation(self):
        raw = "micro-\nstructure analysis"
        assert "microstructure" in clean_extracted_text(raw)

    def test_collapses_spaces(self):
        result = clean_extracted_text("word    another")
        assert "  " not in result

    def test_collapses_excess_newlines(self):
        result = clean_extracted_text("line1\n\n\n\n\nline2")
        assert result.count("\n") <= 2

    def test_strips_non_printable(self):
        result = clean_extracted_text("text\x00\x01\x02more")
        assert "\x00" not in result
        assert "\x01" not in result


class TestTableToText:
    def test_basic_table(self):
        table = [
            ["Treatment", "Strength (MPa)", "Elongation (%)"],
            ["820°C / 2h", "1404", "11"],
            ["840°C / 2h", "1320", "14"],
        ]
        result = _table_to_text(table)
        assert "1404" in result
        assert "820" in result

    def test_empty_table(self):
        assert _table_to_text([]) == ""

    def test_single_row_table(self):
        assert _table_to_text([["Header"]]) == ""

    def test_skips_empty_cells(self):
        table = [["A", "B"], ["val", None]]
        result = _table_to_text(table)
        assert result != ""


# ── API endpoint tests ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_nlp():
    # en_core_web_sm was never an installed dependency (config.spacy_model_en
    # is en_core_sci_sm) — use the same domain model the app actually runs.
    return build_pipeline("en_core_sci_sm")


@pytest.fixture
def mock_graph():
    graph = AsyncMock()
    graph.upsert_entities = AsyncMock()
    graph.upsert_relations = AsyncMock()
    return graph


@pytest_asyncio.fixture
async def client(real_nlp, mock_graph):
    app = FastAPI()
    app.include_router(router)
    app.state.graph = mock_graph

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _make_minimal_pdf(
    text: str = "Ti555211 heat treated at 820°C achieved strength 1404 MPa.",
) -> bytes:
    """Create a minimal valid PDF with given text using pure bytes (no extra deps)."""
    # Minimal hand-crafted PDF structure
    content_stream = f"BT /F1 12 Tf 50 750 Td ({text}) Tj ET"
    stream_bytes = content_stream.encode("latin-1")
    length = len(stream_bytes)

    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 << /Type /Font "
        b"/Subtype /Type1 /BaseFont /Helvetica >> >> >> >>\nendobj\n"
        b"4 0 obj\n<< /Length "
        + str(length).encode()
        + b" >>\nstream\n"
        + stream_bytes
        + b"\nendstream\nendobj\n"
        b"xref\n0 5\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000266 00000 n \n"
        b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n"
        + str(266 + length + 20).encode()
        + b"\n%%EOF"
    )
    return pdf


@pytest.mark.asyncio
async def test_pdf_wrong_extension(client):
    data = io.BytesIO(b"not a pdf")
    resp = await client.post(
        "/api/v1/documents/pdf",
        files={"file": ("report.txt", data, "text/plain")},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pdf_too_large(client):
    large = io.BytesIO(b"x" * (11 * 1024 * 1024))
    resp = await client.post(
        "/api/v1/documents/pdf",
        files={"file": ("big.pdf", large, "application/pdf")},
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_pdf_empty_text(client):
    # PDF that pdfplumber will parse but extract no text from
    with patch("science_kg.api.routes.extract_text_from_pdf", return_value=("", 1)):
        empty_pdf = io.BytesIO(b"%PDF-1.4 minimal")
        resp = await client.post(
            "/api/v1/documents/pdf",
            files={"file": ("empty.pdf", empty_pdf, "application/pdf")},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pdf_valid_extraction(client, mock_graph):
    mock_graph.upsert_entities.reset_mock()
    mock_graph.upsert_relations.reset_mock()

    fake_text = (
        "Ti555211 alloy was heat treated at 820°C. Tensile strength reached 1404 MPa."
    )
    with patch(
        "science_kg.api.routes.extract_text_from_pdf", return_value=(fake_text, 3)
    ):
        pdf_bytes = io.BytesIO(b"%PDF-1.4 fake")
        resp = await client.post(
            "/api/v1/documents/pdf",
            files={"file": ("paper.pdf", pdf_bytes, "application/pdf")},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["filename"] == "paper.pdf"
    assert body["page_count"] == 3
    assert body["language"] in ("ru", "en")
    assert "extraction" in body
    mock_graph.upsert_entities.assert_called_once()
    mock_graph.upsert_relations.assert_called_once()
    mock_graph.upsert_document.assert_called_once()
    assert mock_graph.upsert_document.call_args[0][0] == body["doc_id"]
    assert mock_graph.upsert_document.call_args[0][1] == fake_text
