"""Step 04 - markdown endpoint tests."""

from pathlib import Path

from app.paths import raw_to_stage0_okf, raw_to_stage1_okf


def test_markdown_sets_resolution_headers(api_client, shared_root: Path) -> None:
    raw = "UPLOAD_DATA/reports/q1.pdf"
    target = shared_root / raw
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"raw")
    okf = shared_root / raw_to_stage0_okf(raw)
    okf.parent.mkdir(parents=True, exist_ok=True)
    okf.write_text("---\ntitle: t\n---\nbody", encoding="utf-8")

    response = api_client.get(
        "/api/v1/markdown",
        params={"okf_path": "reports/q1.pdf"},
    )

    assert response.status_code == 200
    assert response.headers["X-Requested-Path"] == "reports/q1.pdf"
    assert response.headers["X-Resolved-Path"] == raw_to_stage0_okf(raw)


def test_markdown_uses_exact_okf_path(api_client, shared_root: Path) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    (shared_root / raw).parent.mkdir(parents=True, exist_ok=True)
    (shared_root / raw).write_bytes(b"raw")
    okf = shared_root / raw_to_stage1_okf(raw)
    okf.parent.mkdir(parents=True, exist_ok=True)
    okf.write_text("---\ntitle: t\n---\nhello", encoding="utf-8")

    response = api_client.get(
        "/api/v1/markdown",
        params={"okf_path": raw_to_stage1_okf(raw)},
    )

    assert response.status_code == 200
    assert "hello" in response.text


def test_markdown_404_when_wrong_stage_path(api_client, shared_root: Path) -> None:
    raw = "UPLOAD_DATA/reports/q1__v01.pdf"
    (shared_root / raw).parent.mkdir(parents=True, exist_ok=True)
    (shared_root / raw).write_bytes(b"raw")
    stage0 = shared_root / raw_to_stage0_okf(raw)
    stage0.parent.mkdir(parents=True, exist_ok=True)
    stage0.write_text("# stage0 only", encoding="utf-8")

    response = api_client.get(
        "/api/v1/markdown",
        params={"okf_path": raw_to_stage1_okf(raw)},
    )

    assert response.status_code == 404
