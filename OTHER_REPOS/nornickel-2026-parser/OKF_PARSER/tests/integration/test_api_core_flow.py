"""Step 04 - core API flow integration tests."""

from pathlib import Path

from app.locks.files import create_worker_lock, worker_lock_path
from app.paths import raw_to_stage0_okf, raw_to_stage1_okf
from app.presentation.schemas import ProcessingStatus


def _seed_raw(shared_root: Path, relative: str, content: bytes = b"raw") -> None:
    target = shared_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def test_upload_process_status_markdown(api_client, shared_root: Path) -> None:
    upload = api_client.post(
        "/api/v1/files/upload",
        data={"path": "reports/q1.pdf"},
        files={"file": ("q1.pdf", b"pdf", "application/pdf")},
    )
    assert upload.status_code == 202
    resolved = upload.json()["resolved_path"]

    process = api_client.post("/api/v1/files/process", json={"path": "reports/q1.pdf"})
    assert process.status_code == 202

    status = api_client.get("/api/v1/files/status", params={"path": "reports/q1.pdf"})
    assert status.status_code == 200
    assert status.json()["resolved_path"] == resolved

    okf = shared_root / raw_to_stage0_okf(resolved)
    okf.parent.mkdir(parents=True, exist_ok=True)
    okf.write_text("---\ntitle: t\n---\ncontent", encoding="utf-8")
    markdown = api_client.get(
        "/api/v1/markdown",
        params={"okf_path": "reports/q1.pdf"},
    )
    assert markdown.status_code == 200
    assert "content" in markdown.text


def test_process_returns_409_without_enforce_when_stage0_exists(
    api_client, shared_root: Path
) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    _seed_raw(shared_root, raw)
    stage0 = shared_root / raw_to_stage0_okf(raw)
    stage0.parent.mkdir(parents=True, exist_ok=True)
    stage0.write_text("# okf", encoding="utf-8")
    response = api_client.post("/api/v1/files/process", json={"path": raw})
    assert response.status_code == 409


def test_process_enforce_true_reprocesses_existing_stage0(
    api_client, shared_root: Path
) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    _seed_raw(shared_root, raw)
    stage0 = shared_root / raw_to_stage0_okf(raw)
    stage0.parent.mkdir(parents=True, exist_ok=True)
    stage0.write_text("# okf", encoding="utf-8")
    response = api_client.post(
        "/api/v1/files/process",
        json={"path": raw, "enforce": True},
    )
    assert response.status_code == 202


def test_reindex_enqueues_all_historical_versions(
    api_client, shared_root: Path
) -> None:
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v02.pdf")
    response = api_client.post("/api/v1/reindex", json={})
    assert response.status_code == 202
    assert response.json()["enqueued"] == 2


def test_status_reports_stale_lock_as_processing(
    api_client, shared_root: Path
) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    _seed_raw(shared_root, raw)
    lock = worker_lock_path(str(shared_root), raw, ".worker.lock")
    create_worker_lock(lock)

    response = api_client.get("/api/v1/files/status", params={"path": raw})
    assert response.status_code == 200
    stages = {item["stage"]: item["status"] for item in response.json()["stages"]}
    assert stages["docling_raw"] == ProcessingStatus.processing.value

    lock.unlink()
