"""Step 10 - full system integration tests."""

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from admin_panel.main import app as panel_app
from app.locks.files import create_worker_lock, worker_lock_path
from app.paths import raw_to_stage0_okf, raw_to_stage1_okf
from tests.integration.test_panel_runtime import _patch_httpx

runner = CliRunner()


def _seed_raw(shared_root: Path, relative: str) -> None:
    target = shared_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"raw")


def test_full_system_happy_path(api_client, shared_root: Path) -> None:
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
    okf.write_text("---\ntitle: t\n---\ncontent", encoding="utf-8")
    assert api_client.get("/api/v1/markdown", params={"okf_path": "reports/q1.pdf"}).status_code == 200


def test_process_enforce_and_409_paths(api_client, shared_root: Path) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    _seed_raw(shared_root, raw)
    stage0 = shared_root / raw_to_stage0_okf(raw)
    stage0.parent.mkdir(parents=True, exist_ok=True)
    stage0.write_text("# okf", encoding="utf-8")
    assert api_client.post("/api/v1/files/process", json={"path": raw}).status_code == 409
    assert (
        api_client.post("/api/v1/files/process", json={"path": raw, "enforce": True}).status_code
        == 202
    )


def test_tree_contract_end_to_end(api_client, shared_root: Path) -> None:
    (shared_root / "UPLOAD_DATA").mkdir(parents=True, exist_ok=True)
    response = api_client.get("/api/v1/files/tree")
    assert response.status_code == 200
    assert response.json()["tree"]["name"] == "SHARED"


def test_reindex_enqueues_all_files(api_client, shared_root: Path) -> None:
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v02.pdf")
    assert api_client.post("/api/v1/reindex", json={}).json()["enqueued"] == 2


def test_stale_lock_and_cleanup_recovery(api_client, shared_root: Path) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    _seed_raw(shared_root, raw)
    lock = worker_lock_path(str(shared_root), raw, ".worker.lock")
    create_worker_lock(lock)
    assert (
        api_client.get("/api/v1/files/status", params={"path": raw}).json()["stages"][0]["status"]
        == "processing"
    )
    lock.unlink(missing_ok=True)
    assert (
        api_client.get("/api/v1/files/status", params={"path": raw}).json()["stages"][0]["status"]
        == "pending"
    )


def test_admin_panel_with_live_services(api_client, config_files, monkeypatch) -> None:
    config_path, env_path = config_files
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    _patch_httpx(api_client, monkeypatch)
    result = runner.invoke(
        panel_app,
        ["once", "--config", str(config_path), "--env-file", str(env_path)],
    )
    assert result.exit_code == 0
    assert "Statistics" in result.stdout
