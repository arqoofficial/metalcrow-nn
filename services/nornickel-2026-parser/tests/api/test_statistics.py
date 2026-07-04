"""Step 04 - statistics endpoint tests."""

from pathlib import Path

from app.paths import raw_to_stage0_okf, raw_to_stage1_okf


def test_statistics_counts_all_versioned_and_simple_files(
    api_client, shared_root: Path
) -> None:
    files = [
        "UPLOAD_DATA/reports/q1__v01.pdf",
        "UPLOAD_DATA/reports/q1__v02.pdf",
        "RAW_DATA/reports/q1__v05.pdf",
        "UPLOAD_DATA/reports/q2__v01.pdf",
    ]
    for relative in files:
        target = shared_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"raw")

    stage0 = shared_root / raw_to_stage0_okf("UPLOAD_DATA/reports/q1__v02.pdf")
    stage0.parent.mkdir(parents=True, exist_ok=True)
    stage0.write_text("# okf", encoding="utf-8")
    stage1 = shared_root / raw_to_stage1_okf("UPLOAD_DATA/reports/q2__v01.pdf")
    stage1.parent.mkdir(parents=True, exist_ok=True)
    stage1.write_text("# okf", encoding="utf-8")

    response = api_client.get("/api/v1/statistics")

    assert response.status_code == 200
    body = response.json()
    assert body["total_raw_files"] == 4
    assert body["stage0_done"] == 1
    assert body["stage1_done"] == 1
    assert body["coverage_ratio"] == 0.25


def test_statistics_counts_simple_and_versioned_siblings(
    api_client, shared_root: Path
) -> None:
    reports = shared_root / "RAW_DATA" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "q1.pdf").write_bytes(b"simple")
    (reports / "q1__v02.pdf").write_bytes(b"versioned")

    response = api_client.get("/api/v1/statistics")

    assert response.status_code == 200
    assert response.json()["total_raw_files"] == 2


def test_statistics_counts_bootstrap_raw_data(api_client, shared_root: Path) -> None:
    target = shared_root / "RAW_DATA" / "reports" / "bootstrap.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"raw")

    response = api_client.get("/api/v1/statistics")

    assert response.status_code == 200
    assert response.json()["total_raw_files"] == 1
