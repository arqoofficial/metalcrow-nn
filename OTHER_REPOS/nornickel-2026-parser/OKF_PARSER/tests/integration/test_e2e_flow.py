"""Step 10 - end-to-end API flow integration tests."""

from pathlib import Path

from app.locks.files import create_worker_lock, worker_lock_path
from app.paths import raw_to_stage0_okf, raw_to_stage1_okf
from app.presentation.schemas import ProcessingStatus


def _seed_raw(shared_root: Path, relative: str, content: bytes = b"raw") -> None:
    target = shared_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def test_upload_process_status_markdown_happy_path(api_client, shared_root: Path) -> None:
    upload = api_client.post(
        "/api/v1/files/upload",
        data={"path": "reports/q1.pdf"},
        files={"file": ("q1.pdf", b"pdf", "application/pdf")},
    )
    assert upload.status_code == 202
    process = api_client.post("/api/v1/files/process", json={"path": "reports/q1.pdf"})
    assert process.status_code == 202
    resolved = process.json()["resolved_path"]
    okf = shared_root / raw_to_stage0_okf(resolved)
    okf.parent.mkdir(parents=True, exist_ok=True)
    okf.write_text("---\ntitle: t\n---\nbody", encoding="utf-8")
    status = api_client.get("/api/v1/files/status", params={"path": "reports/q1.pdf"})
    assert status.status_code == 200
    markdown = api_client.get("/api/v1/markdown", params={"okf_path": "reports/q1.pdf"})
    assert markdown.status_code == 200


def test_process_404_when_exact_version_missing(api_client, shared_root: Path) -> None:
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v03.pdf")
    process = api_client.post(
        "/api/v1/files/process",
        json={"path": "UPLOAD_DATA/reports/q1__v02.pdf"},
    )
    assert process.status_code == 404


def test_process_409_without_enforce(api_client, shared_root: Path) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    _seed_raw(shared_root, raw)
    stage0 = shared_root / raw_to_stage0_okf(raw)
    stage0.parent.mkdir(parents=True, exist_ok=True)
    stage0.write_text("# okf", encoding="utf-8")
    response = api_client.post("/api/v1/files/process", json={"path": raw})
    assert response.status_code == 409


def test_duplicate_inflight_jobs_behavior(api_client, shared_root: Path) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    _seed_raw(shared_root, raw)
    first = api_client.post("/api/v1/files/process", json={"path": raw})
    second = api_client.post("/api/v1/files/process", json={"path": raw})
    assert first.status_code == 202
    assert second.status_code == 202
