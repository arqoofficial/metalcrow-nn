"""Step 03 - clean_lock.sh tests."""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLEAN_LOCK_SCRIPT = REPO_ROOT / "clean_lock.sh"


def _run_clean_lock(shared_root: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SHARED_ROOT"] = str(shared_root)
    return subprocess.run(
        [str(CLEAN_LOCK_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_clean_lock_removes_both_patterns(tmp_path: Path) -> None:
    upload_lock = tmp_path / "UPLOAD_DATA" / "reports" / "q1__v01.pdf.upload.lock"
    worker_lock = tmp_path / "UPLOAD_DATA" / "reports" / "q1__v01.pdf.worker.lock"
    upload_lock.parent.mkdir(parents=True)
    upload_lock.write_text("upload", encoding="utf-8")
    worker_lock.write_text("worker", encoding="utf-8")

    result = _run_clean_lock(tmp_path)

    assert result.returncode == 0
    assert not upload_lock.exists()
    assert not worker_lock.exists()


def test_clean_lock_tolerates_missing_files(tmp_path: Path) -> None:
    result = _run_clean_lock(tmp_path)
    assert result.returncode == 0

    missing_root = tmp_path / "does-not-exist"
    result = _run_clean_lock(missing_root)
    assert result.returncode == 0
