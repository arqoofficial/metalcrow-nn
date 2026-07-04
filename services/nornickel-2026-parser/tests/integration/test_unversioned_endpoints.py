"""All user-facing API endpoints must work with simple filenames (no __vNN)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.paths import raw_to_stage0_okf

SIMPLE_RAW = "RAW_DATA/reports/bootstrap.pdf"


def _seed_raw(shared_root: Path, relative: str, content: bytes = b"raw") -> None:
    target = shared_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


@pytest.fixture()
def bootstrap_file(shared_root: Path) -> str:
    _seed_raw(shared_root, SIMPLE_RAW)
    return SIMPLE_RAW


def test_statistics_counts_simple_filename(api_client, bootstrap_file: str) -> None:
    response = api_client.get("/api/v1/statistics")
    assert response.status_code == 200
    assert response.json()["total_raw_files"] >= 1


def test_tree_lists_simple_filename(api_client, bootstrap_file: str) -> None:
    response = api_client.get(
        "/api/v1/files/tree",
        params={"root": "RAW_DATA/reports"},
    )
    assert response.status_code == 200
    names = {
        child["name"]
        for child in response.json()["tree"]["children"][0]["children"][0]["children"]
    }
    assert "bootstrap.pdf" in names


def test_process_accepts_simple_concrete_path(api_client, bootstrap_file: str) -> None:
    response = api_client.post(
        "/api/v1/files/process",
        json={"path": SIMPLE_RAW},
    )
    assert response.status_code == 202
    assert response.json()["resolved_path"] == SIMPLE_RAW


def test_status_accepts_simple_concrete_path(api_client, bootstrap_file: str) -> None:
    response = api_client.get(
        "/api/v1/files/status",
        params={"path": SIMPLE_RAW},
    )
    assert response.status_code == 200
    assert response.json()["resolved_path"] == SIMPLE_RAW


def test_markdown_reads_okf_for_simple_filename(
    api_client, shared_root: Path, bootstrap_file: str
) -> None:
    okf = shared_root / raw_to_stage0_okf(SIMPLE_RAW)
    okf.parent.mkdir(parents=True, exist_ok=True)
    okf.write_text("# bootstrap okf\n", encoding="utf-8")

    response = api_client.get(
        "/api/v1/markdown",
        params={"okf_path": raw_to_stage0_okf(SIMPLE_RAW)},
    )
    assert response.status_code == 200
    assert "bootstrap okf" in response.text


def test_reindex_enqueues_simple_filename(api_client, bootstrap_file: str) -> None:
    response = api_client.post("/api/v1/reindex", json={})
    assert response.status_code == 202
    assert response.json()["enqueued"] >= 1


def test_validate_path_accepts_simple_concrete_path(api_client, bootstrap_file: str) -> None:
    response = api_client.get(
        "/api/v1/validate/path",
        params={"path": SIMPLE_RAW},
    )
    assert response.status_code == 200


def test_upload_first_uses_simple_name_then_versions(
    api_client, shared_root: Path, bootstrap_file: str
) -> None:
    first = api_client.post(
        "/api/v1/files/upload",
        data={"path": "reports/other.pdf"},
        files={"file": ("other.pdf", b"first", "application/pdf")},
    )
    assert first.status_code == 202
    assert first.json()["resolved_path"] == "UPLOAD_DATA/reports/other.pdf"

    second = api_client.post(
        "/api/v1/files/upload",
        data={"path": "reports/other.pdf"},
        files={"file": ("other.pdf", b"second", "application/pdf")},
    )
    assert second.status_code == 202
    assert second.json()["resolved_path"] == "UPLOAD_DATA/reports/other__v01.pdf"


def test_direct_simple_path_when_versioned_sibling_exists(
    api_client, shared_root: Path
) -> None:
    reports = shared_root / "RAW_DATA" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "bootstrap.pdf").write_bytes(b"simple")
    (reports / "bootstrap__v02.pdf").write_bytes(b"versioned")

    process = api_client.post(
        "/api/v1/files/process",
        json={"path": SIMPLE_RAW},
    )
    assert process.status_code == 202
    assert process.json()["resolved_path"] == SIMPLE_RAW


def test_logical_path_hits_exact_simple_name(
    api_client, shared_root: Path
) -> None:
    reports = shared_root / "RAW_DATA" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "bootstrap.pdf").write_bytes(b"simple")
    (reports / "bootstrap__v02.pdf").write_bytes(b"versioned")

    process = api_client.post(
        "/api/v1/files/process",
        json={"path": "reports/bootstrap.pdf"},
    )
    assert process.status_code == 202
    assert process.json()["resolved_path"] == "RAW_DATA/reports/bootstrap.pdf"
