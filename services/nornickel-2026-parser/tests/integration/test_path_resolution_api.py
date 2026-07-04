"""Step 04 - path resolution integration through API."""

from pathlib import Path

from app.paths import raw_to_stage0_okf


def _seed_raw(shared_root: Path, relative: str) -> None:
    target = shared_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"raw")


def test_process_logical_path_hits_exact_file(api_client, shared_root: Path) -> None:
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1.pdf")
    response = api_client.post(
        "/api/v1/files/process",
        json={"path": "reports/q1.pdf"},
    )
    assert response.status_code == 202
    assert response.json()["resolved_path"] == "UPLOAD_DATA/reports/q1.pdf"


def test_status_concrete_path_remains_exact(api_client, shared_root: Path) -> None:
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v01.pdf")
    _seed_raw(shared_root, "UPLOAD_DATA/reports/q1__v03.pdf")
    response = api_client.get(
        "/api/v1/files/status",
        params={"path": "UPLOAD_DATA/reports/q1__v01.pdf"},
    )
    assert response.status_code == 200
    assert response.json()["resolved_path"] == "UPLOAD_DATA/reports/q1__v01.pdf"


def test_markdown_exact_okf_path_only(api_client, shared_root: Path) -> None:
    raw = "UPLOAD_DATA/reports/q1__v03.pdf"
    _seed_raw(shared_root, raw)
    okf = shared_root / raw_to_stage0_okf(raw)
    okf.parent.mkdir(parents=True, exist_ok=True)
    okf.write_text("---\ntitle: t\n---\nexact", encoding="utf-8")

    response = api_client.get(
        "/api/v1/markdown",
        params={"okf_path": raw_to_stage0_okf(raw)},
    )
    assert response.status_code == 200
    assert "exact" in response.text

    missing = api_client.get(
        "/api/v1/markdown",
        params={"okf_path": raw_to_stage0_okf("UPLOAD_DATA/reports/q1__v01.pdf")},
    )
    assert missing.status_code == 404
