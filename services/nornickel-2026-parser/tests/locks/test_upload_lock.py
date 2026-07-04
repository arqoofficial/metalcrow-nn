"""Step 03 - upload lock tests."""

import pytest

from app.locks.files import create_upload_lock, remove_lock, upload_lock_path


def test_upload_lock_create_remove(tmp_path) -> None:
    resolved = "UPLOAD_DATA/reports/q1__v01.pdf"
    lock_path = upload_lock_path(str(tmp_path), resolved, ".upload.lock")

    create_upload_lock(lock_path)
    assert lock_path.is_file()
    assert not lock_path.is_dir()

    with pytest.raises(FileExistsError):
        create_upload_lock(lock_path)

    remove_lock(lock_path)
    assert not lock_path.exists()

    remove_lock(lock_path)
