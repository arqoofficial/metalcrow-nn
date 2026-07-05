"""Step 02 - raw to stage OKF path mapping."""

from app.paths import raw_to_stage0_okf, raw_to_stage1_okf


def test_raw_to_stage0_okf_mapping() -> None:
    raw = "UPLOAD_DATA/reports/q1__v02.pdf"
    assert raw_to_stage0_okf(raw) == "00_docling_raw/UPLOAD_DATA/reports/q1__v02.pdf.md"


def test_raw_to_stage1_okf_mapping() -> None:
    raw = "UPLOAD_DATA/reports/q1__v02.pdf"
    assert raw_to_stage1_okf(raw) == "01_docling_clean00/UPLOAD_DATA/reports/q1__v02.pdf.md"
