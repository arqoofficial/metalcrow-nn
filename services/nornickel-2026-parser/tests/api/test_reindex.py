"""Step 04 - reindex endpoint tests."""

from pathlib import Path


def _seed_raw(shared_root: Path, relative: str) -> None:
    target = shared_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"raw")


def test_reindex_enqueues_all_files(api_client, shared_root: Path) -> None:
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v02.pdf")

    response = api_client.post("/api/v1/reindex", json={})

    assert response.status_code == 202
    assert response.json()["enqueued"] == 2
