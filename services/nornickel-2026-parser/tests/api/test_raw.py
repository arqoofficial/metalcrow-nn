"""Raw file download endpoint tests."""

from pathlib import Path


from app.presentation.http_headers import decode_path_header


def test_raw_sets_resolution_headers(api_client, shared_root: Path) -> None:
    raw = "UPLOAD_DATA/reports/q1.pdf"
    target = shared_root / raw
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"%PDF-1.4 raw bytes")

    response = api_client.get(
        "/api/v1/files/raw",
        params={"path": "reports/q1.pdf"},
    )

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 raw bytes"
    assert decode_path_header(response.headers["X-Requested-Path"]) == "reports/q1.pdf"
    assert decode_path_header(response.headers["X-Resolved-Path"]) == raw


def test_raw_uses_concrete_path(api_client, shared_root: Path) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    target = shared_root / raw
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"versioned raw")

    response = api_client.get(
        "/api/v1/files/raw",
        params={"path": raw},
    )

    assert response.status_code == 200
    assert response.content == b"versioned raw"
    assert decode_path_header(response.headers["X-Resolved-Path"]) == raw


def test_raw_supports_non_ascii_paths(api_client, shared_root: Path) -> None:
    raw = "RAW_DATA/Доклады/доклад.pdf"
    target = shared_root / raw
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"%PDF raw")

    response = api_client.get(
        "/api/v1/files/raw",
        params={"path": raw},
    )

    assert response.status_code == 200
    assert response.content == b"%PDF raw"
    assert decode_path_header(response.headers["X-Resolved-Path"]) == raw


def test_raw_404_when_missing(api_client) -> None:
    response = api_client.get(
        "/api/v1/files/raw",
        params={"path": "reports/missing.pdf"},
    )

    assert response.status_code == 404
