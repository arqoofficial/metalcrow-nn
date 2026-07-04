"""Step 05 - tree endpoint integration tests."""

import os
from pathlib import Path


def _mkdir(shared_root: Path, *parts: str) -> Path:
    target = shared_root.joinpath(*parts)
    target.mkdir(parents=True, exist_ok=True)
    return target


def test_tree_root_request(api_client, shared_root: Path) -> None:
    _mkdir(shared_root, "RAW_DATA")
    response = api_client.get("/api/v1/files/tree")
    assert response.status_code == 200


def test_tree_subtree_request(api_client, shared_root: Path) -> None:
    _mkdir(shared_root, "UPLOAD_DATA", "reports")
    response = api_client.get("/api/v1/files/tree", params={"root": "UPLOAD_DATA/reports"})
    assert response.status_code == 200
    assert response.json()["resolved_root"] == "UPLOAD_DATA/reports"


def test_tree_root_normalization_with_warnings(api_client, shared_root: Path) -> None:
    _mkdir(shared_root, "UPLOAD_DATA")
    response = api_client.get("/api/v1/files/tree", params={"root": "/UPLOAD_DATA"})
    assert response.status_code == 200
    assert response.json()["warnings"]


def test_tree_outside_shared_returns_400(api_client) -> None:
    response = api_client.get("/api/v1/files/tree", params={"root": ".."})
    assert response.status_code == 400


def test_tree_missing_subtree_returns_404(api_client) -> None:
    response = api_client.get("/api/v1/files/tree", params={"root": "nope"})
    assert response.status_code == 404


def test_tree_limit_and_depth_bounds(api_client, shared_root: Path) -> None:
    assert api_client.get("/api/v1/files/tree", params={"limit": 1001}).status_code == 400
    assert api_client.get("/api/v1/files/tree", params={"max_depth": 11}).status_code == 400


def test_tree_hides_hidden_and_lock_files(api_client, shared_root: Path) -> None:
    reports = _mkdir(shared_root, "UPLOAD_DATA", "reports")
    (reports / ".secret").write_bytes(b"x")
    (reports / "file.worker.lock").write_bytes(b"x")
    response = api_client.get("/api/v1/files/tree", params={"root": "UPLOAD_DATA/reports"})
    names = {
        child["name"]
        for child in response.json()["tree"]["children"][0]["children"][0]["children"]
    }
    assert ".secret" not in names
    assert "file.worker.lock" not in names


def test_tree_does_not_follow_symlinks(api_client, shared_root: Path) -> None:
    base = _mkdir(shared_root, "UPLOAD_DATA", "reports")
    target = _mkdir(shared_root, "UPLOAD_DATA", "other")
    (target / "deep.txt").write_bytes(b"x")
    os.symlink(target, base / "sym", target_is_directory=True)
    response = api_client.get("/api/v1/files/tree", params={"root": "UPLOAD_DATA/reports"})
    sym = next(
        child
        for child in response.json()["tree"]["children"][0]["children"][0]["children"]
        if child["name"] == "sym"
    )
    assert sym["type"] == "file"


def test_tree_offset_limit_pagination_contract(api_client, shared_root: Path) -> None:
    upload = _mkdir(shared_root, "UPLOAD_DATA")
    for index in range(3):
        (upload / f"item-{index}.txt").write_bytes(b"x")
    first = api_client.get(
        "/api/v1/files/tree",
        params={"root": "UPLOAD_DATA", "limit": 1, "offset": 0, "max_depth": 0},
    )
    second = api_client.get(
        "/api/v1/files/tree",
        params={"root": "UPLOAD_DATA", "limit": 1, "offset": 1, "max_depth": 0},
    )
    first_name = first.json()["tree"]["children"][0]["children"][0]["name"]
    second_name = second.json()["tree"]["children"][0]["children"][0]["name"]
    assert first_name != second_name
