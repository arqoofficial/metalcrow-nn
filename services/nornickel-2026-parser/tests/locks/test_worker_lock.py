"""Step 03 - worker lock tests."""

from app.locks.files import create_worker_lock, remove_lock, worker_lock_path


def test_worker_lock_create_remove(tmp_path) -> None:
    resolved = "UPLOAD_DATA/reports/q1__v01.pdf"
    lock_path = worker_lock_path(str(tmp_path), resolved, ".worker.lock")

    create_worker_lock(lock_path)
    assert lock_path.is_file()

    remove_lock(lock_path)
    assert not lock_path.exists()


def test_worker_lock_uses_resolved_path_key(tmp_path) -> None:
    resolved = "UPLOAD_DATA/reports/q1__v01.pdf"
    lock_path = worker_lock_path(str(tmp_path), resolved, ".worker.lock")

    create_worker_lock(lock_path)

    assert lock_path.name == "q1__v01.pdf.worker.lock"
    assert "00_docling_raw" not in str(lock_path)

    remove_lock(lock_path)
