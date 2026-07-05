"""Step 04 - upload endpoint tests."""

from pathlib import Path


def test_upload_accepts_logical_path_only(api_client, shared_root: Path) -> None:
    response = api_client.post(
        "/api/v1/files/upload",
        data={"path": "reports/q1.pdf"},
        files={"file": ("q1.pdf", b"pdf-bytes", "application/pdf")},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["requested_path"] == "reports/q1.pdf"
    assert body["resolved_path"] == "UPLOAD_DATA/reports/q1.pdf"


def test_upload_uses_simple_name_when_no_file_exists(
    api_client, shared_root: Path
) -> None:
    response = api_client.post(
        "/api/v1/files/upload",
        data={"path": "reports/q1.pdf"},
        files={"file": ("q1.pdf", b"first-bytes", "application/pdf")},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["resolved_path"] == "UPLOAD_DATA/reports/q1.pdf"
    assert (shared_root / body["resolved_path"]).read_bytes() == b"first-bytes"


def test_upload_allocates_version_when_simple_file_exists(
    api_client, shared_root: Path
) -> None:
    existing = shared_root / "UPLOAD_DATA" / "reports" / "q1.pdf"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"old")

    response = api_client.post(
        "/api/v1/files/upload",
        data={"path": "reports/q1.pdf"},
        files={"file": ("q1.pdf", b"new-bytes", "application/pdf")},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["resolved_path"] == "UPLOAD_DATA/reports/q1__v01.pdf"
    assert (shared_root / body["resolved_path"]).read_bytes() == b"new-bytes"


def test_upload_allocates_next_version_when_versions_exist(
    api_client, shared_root: Path
) -> None:
    reports = shared_root / "UPLOAD_DATA" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "q1__v01.pdf").write_bytes(b"old")

    response = api_client.post(
        "/api/v1/files/upload",
        data={"path": "reports/q1.pdf"},
        files={"file": ("q1.pdf", b"new-bytes", "application/pdf")},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["resolved_path"] == "UPLOAD_DATA/reports/q1__v02.pdf"


def test_upload_rejects_concrete_path(api_client) -> None:
    response = api_client.post(
        "/api/v1/files/upload",
        data={"path": "UPLOAD_DATA/reports/q1__v01.pdf"},
        files={"file": ("q1.pdf", b"pdf-bytes", "application/pdf")},
    )
    assert response.status_code == 400


def test_upload_rejects_archive_path(api_client) -> None:
    response = api_client.post(
        "/api/v1/files/upload",
        data={"path": "reports/bundle.zip"},
        files={"file": ("bundle.zip", b"PK", "application/zip")},
    )
    assert response.status_code == 400
    assert "archive" in response.json()["detail"].lower()


def test_upload_rejects_unsupported_document_type(api_client) -> None:
    response = api_client.post(
        "/api/v1/files/upload",
        data={"path": "reports/legacy.xls"},
        files={"file": ("legacy.xls", b"xls", "application/vnd.ms-excel")},
    )
    assert response.status_code == 400
    assert "docling" in response.json()["detail"].lower()
