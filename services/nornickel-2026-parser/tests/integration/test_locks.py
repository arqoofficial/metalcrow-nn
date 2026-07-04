"""Step 10 - lock lifecycle integration tests."""

import os
import subprocess
from pathlib import Path

from app.locks.files import create_worker_lock, worker_lock_path
from app.presentation.schemas import ProcessingStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
CLEAN_LOCK_SCRIPT = REPO_ROOT / "clean_lock.sh"


def _seed_raw(shared_root: Path, relative: str) -> None:
    target = shared_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"raw")


def test_stale_lock_reports_processing_until_cleanup(api_client, shared_root: Path) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    _seed_raw(shared_root, raw)
    lock = worker_lock_path(str(shared_root), raw, ".worker.lock")
    create_worker_lock(lock)
    response = api_client.get("/api/v1/files/status", params={"path": raw})
    assert response.status_code == 200
    stages = {item["stage"]: item["status"] for item in response.json()["stages"]}
    assert stages["docling_raw"] == ProcessingStatus.processing.value


def test_clean_lock_restores_progression(api_client, shared_root: Path) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    _seed_raw(shared_root, raw)
    lock = worker_lock_path(str(shared_root), raw, ".worker.lock")
    create_worker_lock(lock)
    env = os.environ.copy()
    env["SHARED_ROOT"] = str(shared_root)
    subprocess.run([str(CLEAN_LOCK_SCRIPT)], env=env, check=True)
    response = api_client.get("/api/v1/files/status", params={"path": raw})
    stages = {item["stage"]: item["status"] for item in response.json()["stages"]}
    assert stages["docling_raw"] == ProcessingStatus.pending.value
