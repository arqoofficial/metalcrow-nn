"""Step 04 - process endpoint tests."""

from pathlib import Path

from app.paths import raw_to_stage0_okf


def _seed_raw(shared_root: Path, relative: str, content: bytes = b"raw") -> None:
    target = shared_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def test_process_exact_path_when_exists(api_client, shared_root: Path) -> None:
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v02.pdf")
    response = api_client.post(
        "/api/v1/files/process",
        json={"path": "UPLOAD_DATA/reports/q1__v02.pdf"},
    )
    assert response.status_code == 202
    assert response.json()["resolved_path"] == "UPLOAD_DATA/reports/q1__v02.pdf"


def test_process_404_when_exact_path_missing(
    api_client, shared_root: Path
) -> None:
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v03.pdf")
    response = api_client.post(
        "/api/v1/files/process",
        json={"path": "UPLOAD_DATA/reports/q1__v01.pdf"},
    )
    assert response.status_code == 404


def test_process_returns_409_when_stage0_exists_and_enforce_false(
    api_client, shared_root: Path
) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    _seed_raw(shared_root, raw)
    stage0 = shared_root / raw_to_stage0_okf(raw)
    stage0.parent.mkdir(parents=True, exist_ok=True)
    stage0.write_text("# okf\n", encoding="utf-8")

    response = api_client.post(
        "/api/v1/files/process",
        json={"path": raw, "enforce": False},
    )
    assert response.status_code == 409


def test_process_enqueues_when_enforce_true(api_client, shared_root: Path) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    _seed_raw(shared_root, raw)
    stage0 = shared_root / raw_to_stage0_okf(raw)
    stage0.parent.mkdir(parents=True, exist_ok=True)
    stage0.write_text("# okf\n", encoding="utf-8")

    response = api_client.post(
        "/api/v1/files/process",
        json={"path": raw, "enforce": True},
    )
    assert response.status_code == 202


def test_process_allows_duplicate_in_flight_when_output_absent(
    api_client, shared_root: Path
) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    _seed_raw(shared_root, raw)
    first = api_client.post("/api/v1/files/process", json={"path": raw})
    second = api_client.post("/api/v1/files/process", json={"path": raw})
    assert first.status_code == 202
    assert second.status_code == 202


def test_process_404_when_no_version_exists(api_client) -> None:
    response = api_client.post(
        "/api/v1/files/process",
        json={"path": "reports/missing.pdf"},
    )
    assert response.status_code == 404
