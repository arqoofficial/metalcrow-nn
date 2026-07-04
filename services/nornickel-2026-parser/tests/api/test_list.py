"""Tests for GET /api/v1/files/list."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _mkdir(shared_root: Path, *parts: str) -> Path:
    target = shared_root.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def test_list_raw_data_pdfs_flat_and_search(api_client: TestClient, shared_root: Path) -> None:
    alpha = _mkdir(shared_root, "RAW_DATA", "Доклады", "alpha.pdf")
    beta = _mkdir(shared_root, "RAW_DATA", "reports", "beta.pdf")
    gamma = _mkdir(shared_root, "RAW_DATA", "reports", "gamma.docx")
    upload = _mkdir(shared_root, "UPLOAD_DATA", "metalcrow", "upload.pdf")
    alpha.write_bytes(b"%PDF-1.4 alpha")
    beta.write_bytes(b"%PDF-1.4 beta")
    gamma.write_bytes(b"docx")
    upload.write_bytes(b"%PDF-1.4 upload")

    parsed_okf = _mkdir(
        shared_root,
        "00_docling_raw",
        "RAW_DATA",
        "Доклады",
        "alpha.pdf.md",
    )
    parsed_okf.write_text("---\n---\n# alpha")

    response = api_client.get(
        "/api/v1/files/list",
        params={"source": "RAW_DATA", "extension": ".pdf", "unparsed_only": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert [item["path"] for item in body["data"]] == ["RAW_DATA/reports/beta.pdf"]

    all_pdfs = api_client.get(
        "/api/v1/files/list",
        params={"source": "RAW_DATA", "extension": ".pdf", "unparsed_only": False},
    )
    assert all_pdfs.status_code == 200
    assert all_pdfs.json()["count"] == 2

    search = api_client.get(
        "/api/v1/files/list",
        params={"source": "RAW_DATA", "search": "доклад", "extension": ".pdf"},
    )
    assert search.status_code == 200
    search_body = search.json()
    assert search_body["count"] == 0


def test_list_rejects_unknown_source(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/files/list", params={"source": "MODELS"})
    assert response.status_code == 400
