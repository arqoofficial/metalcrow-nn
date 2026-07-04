"""Step 05 - files tree endpoint tests."""

import os
from pathlib import Path


def _mkdir(shared_root: Path, *parts: str) -> Path:
    target = shared_root.joinpath(*parts)
    target.mkdir(parents=True, exist_ok=True)
    return target


def test_tree_root_implicit_shared(api_client, shared_root: Path) -> None:
    _mkdir(shared_root, "UPLOAD_DATA")
    response = api_client.get("/api/v1/files/tree")
    assert response.status_code == 200
    body = response.json()
    assert body["tree"]["name"] == "SHARED"
    assert any(child["name"] == "UPLOAD_DATA" for child in body["tree"]["children"])


def test_tree_subtree_relative_to_shared(api_client, shared_root: Path) -> None:
    _mkdir(shared_root, "UPLOAD_DATA", "reports")
    response = api_client.get("/api/v1/files/tree", params={"root": "UPLOAD_DATA"})
    assert response.status_code == 200
    body = response.json()
    assert body["resolved_root"] == "UPLOAD_DATA"
    assert body["tree"]["children"][0]["name"] == "UPLOAD_DATA"


def test_tree_default_max_depth_and_limit(api_client, shared_root: Path) -> None:
    response = api_client.get("/api/v1/files/tree")
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 10
    assert body["offset"] == 0


def test_tree_include_files_and_dirs_flags(api_client, shared_root: Path) -> None:
    reports = _mkdir(shared_root, "UPLOAD_DATA", "reports")
    (reports / "q1.pdf").write_bytes(b"x")
    response = api_client.get(
        "/api/v1/files/tree",
        params={"root": "UPLOAD_DATA/reports", "include_dirs": "false"},
    )
    assert response.status_code == 200
    children = response.json()["tree"]["children"][0]["children"][0]["children"]
    assert all(child["type"] == "file" for child in children)


def test_tree_normalizes_recoverable_root_with_200_and_warnings(
    api_client, shared_root: Path
) -> None:
    _mkdir(shared_root, "UPLOAD_DATA")
    response = api_client.get("/api/v1/files/tree", params={"root": "//UPLOAD_DATA//"})
    assert response.status_code == 200
    assert response.json()["resolved_root"] == "UPLOAD_DATA"
    assert response.json()["warnings"]


def test_tree_rejects_outside_shared_with_400(api_client) -> None:
    response = api_client.get("/api/v1/files/tree", params={"root": "../outside"})
    assert response.status_code == 400


def test_tree_missing_subtree_returns_404(api_client) -> None:
    response = api_client.get("/api/v1/files/tree", params={"root": "missing/path"})
    assert response.status_code == 404


def test_tree_limit_bound_1000(api_client, shared_root: Path) -> None:
    response = api_client.get("/api/v1/files/tree", params={"limit": 1001})
    assert response.status_code == 400


def test_tree_max_depth_bound_10(api_client, shared_root: Path) -> None:
    response = api_client.get("/api/v1/files/tree", params={"max_depth": 11})
    assert response.status_code == 400


def test_tree_hides_hidden_files(api_client, shared_root: Path) -> None:
    reports = _mkdir(shared_root, "UPLOAD_DATA", "reports")
    (reports / ".hidden").write_bytes(b"x")
    (reports / "visible.txt").write_bytes(b"x")
    response = api_client.get("/api/v1/files/tree", params={"root": "UPLOAD_DATA/reports"})
    names = {
        child["name"]
        for child in response.json()["tree"]["children"][0]["children"][0]["children"]
    }
    assert ".hidden" not in names
    assert "visible.txt" in names


def test_tree_hides_lock_files_always(api_client, shared_root: Path) -> None:
    reports = _mkdir(shared_root, "UPLOAD_DATA", "reports")
    (reports / "q1.pdf.upload.lock").write_bytes(b"x")
    (reports / "q1.pdf").write_bytes(b"x")
    response = api_client.get("/api/v1/files/tree", params={"root": "UPLOAD_DATA/reports"})
    names = {
        child["name"]
        for child in response.json()["tree"]["children"][0]["children"][0]["children"]
    }
    assert "q1.pdf.upload.lock" not in names


def test_tree_does_not_follow_symlinks(api_client, shared_root: Path) -> None:
    base = _mkdir(shared_root, "UPLOAD_DATA", "reports")
    target = _mkdir(shared_root, "UPLOAD_DATA", "nested")
    (target / "inside.txt").write_bytes(b"x")
    os.symlink(target, base / "linkdir", target_is_directory=True)
    response = api_client.get(
        "/api/v1/files/tree",
        params={"root": "UPLOAD_DATA/reports", "max_depth": 2},
    )
    children = response.json()["tree"]["children"][0]["children"][0]["children"]
    link = next(child for child in children if child["name"] == "linkdir")
    assert link["type"] == "file"
    assert link["children"] == []


def test_tree_root_level_pagination_only(api_client, shared_root: Path) -> None:
    upload = _mkdir(shared_root, "UPLOAD_DATA")
    for index in range(5):
        (upload / f"f{index}.txt").write_bytes(b"x")
    response = api_client.get(
        "/api/v1/files/tree",
        params={"root": "UPLOAD_DATA", "limit": 2, "offset": 0, "max_depth": 0},
    )
    paged = response.json()["tree"]["children"][0]["children"]
    assert len(paged) == 2


def test_tree_ordering_is_lexicographic(api_client, shared_root: Path) -> None:
    upload = _mkdir(shared_root, "UPLOAD_DATA")
    for name in ("b.txt", "a.txt", "c.txt"):
        (upload / name).write_bytes(b"x")
    response = api_client.get(
        "/api/v1/files/tree",
        params={"root": "UPLOAD_DATA", "max_depth": 0},
    )
    names = [child["name"] for child in response.json()["tree"]["children"][0]["children"]]
    assert names == sorted(names)


def test_tree_has_more_and_next_offset(api_client, shared_root: Path) -> None:
    upload = _mkdir(shared_root, "UPLOAD_DATA")
    for index in range(4):
        (upload / f"f{index}.txt").write_bytes(b"x")
    response = api_client.get(
        "/api/v1/files/tree",
        params={"root": "UPLOAD_DATA", "limit": 2, "offset": 0, "max_depth": 0},
    )
    body = response.json()
    assert body["has_more"] is True
    assert body["next_offset"] == 2


def test_tree_response_schema_fields(api_client, shared_root: Path) -> None:
    _mkdir(shared_root, "UPLOAD_DATA")
    response = api_client.get("/api/v1/files/tree")
    body = response.json()
    for field in (
        "requested_root",
        "resolved_root",
        "offset",
        "limit",
        "has_more",
        "next_offset",
        "warnings",
        "generated_at",
        "tree",
    ):
        assert field in body
