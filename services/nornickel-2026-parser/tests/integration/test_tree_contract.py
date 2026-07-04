"""Step 10 - tree endpoint contract integration tests."""

import os
from pathlib import Path


def _mkdir(shared_root: Path, *parts: str) -> Path:
    target = shared_root.joinpath(*parts)
    target.mkdir(parents=True, exist_ok=True)
    return target


def test_tree_subtree_bounds_and_boundary(api_client, shared_root: Path) -> None:
    _mkdir(shared_root, "UPLOAD_DATA")
    assert api_client.get("/api/v1/files/tree", params={"limit": 1001}).status_code == 400
    assert api_client.get("/api/v1/files/tree", params={"max_depth": 11}).status_code == 400
    assert api_client.get("/api/v1/files/tree", params={"root": "../outside"}).status_code == 400
    assert api_client.get("/api/v1/files/tree", params={"root": "UPLOAD_DATA"}).status_code == 200


def test_tree_hidden_lock_symlink_rules(api_client, shared_root: Path) -> None:
    reports = _mkdir(shared_root, "UPLOAD_DATA", "reports")
    (reports / ".hidden").write_bytes(b"x")
    (reports / "q1.pdf.upload.lock").write_bytes(b"x")
    (reports / "visible.txt").write_bytes(b"x")
    target = _mkdir(shared_root, "UPLOAD_DATA", "nested")
    (target / "inside.txt").write_bytes(b"x")
    os.symlink(target, reports / "sym", target_is_directory=True)
    response = api_client.get("/api/v1/files/tree", params={"root": "UPLOAD_DATA/reports"})
    assert response.status_code == 200
    names = {
        child["name"]
        for child in response.json()["tree"]["children"][0]["children"][0]["children"]
    }
    assert ".hidden" not in names
    assert "q1.pdf.upload.lock" not in names
    sym = next(child for child in response.json()["tree"]["children"][0]["children"][0]["children"] if child["name"] == "sym")
    assert sym["type"] == "file"
