"""Step 04 - status endpoint tests."""

from pathlib import Path


def _seed_raw(shared_root: Path, relative: str) -> None:
    target = shared_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"raw")


def test_status_logical_hits_exact_upload_path(api_client, shared_root: Path) -> None:
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1.pdf")
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v03.pdf")

    response = api_client.get("/api/v1/files/status", params={"path": "reports/q1.pdf"})

    assert response.status_code == 200
    assert response.json()["resolved_path"] == "UPLOAD_DATA/reports/q1.pdf"


def test_status_concrete_returns_exact_version(api_client, shared_root: Path) -> None:
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v03.pdf")

    response = api_client.get(
        "/api/v1/files/status",
        params={"path": "UPLOAD_DATA/reports/q1__v01.pdf"},
    )

    assert response.status_code == 200
    assert response.json()["resolved_path"] == "UPLOAD_DATA/reports/q1__v01.pdf"
