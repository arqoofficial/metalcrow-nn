"""Step 02 - logical and concrete path parsing tests."""

from app.paths import (
    ConcreteOkfPath,
    ConcreteRawPath,
    LogicalPath,
    parse_concrete_okf_path,
    parse_concrete_raw_path,
    parse_logical_path,
)


def test_parse_logical_path() -> None:
    parsed = parse_logical_path("reports/q1.pdf")
    assert parsed == LogicalPath(directory="reports", stem="q1", extension=".pdf")
    assert parsed.relative == "reports/q1.pdf"


def test_parse_concrete_raw_path() -> None:
    parsed = parse_concrete_raw_path("UPLOAD_DATA/reports/q1__v02.pdf")
    assert parsed == ConcreteRawPath(
        source="UPLOAD_DATA",
        directory="reports",
        stem="q1",
        extension=".pdf",
        version=2,
    )
    assert parsed.relative == "UPLOAD_DATA/reports/q1__v02.pdf"


def test_parse_bootstrap_raw_path_without_version() -> None:
    parsed = parse_concrete_raw_path("RAW_DATA/Доклады/report.pdf")
    assert parsed == ConcreteRawPath(
        source="RAW_DATA",
        directory="Доклады",
        stem="report",
        extension=".pdf",
        version=1,
        versioned=False,
    )
    assert parsed.relative == "RAW_DATA/Доклады/report.pdf"


def test_parse_unversioned_upload_data_path() -> None:
    parsed = parse_concrete_raw_path("UPLOAD_DATA/reports/q1.pdf")
    assert parsed == ConcreteRawPath(
        source="UPLOAD_DATA",
        directory="reports",
        stem="q1",
        extension=".pdf",
        version=1,
        versioned=False,
    )
    assert parsed.relative == "UPLOAD_DATA/reports/q1.pdf"


def test_parse_concrete_okf_path() -> None:
    parsed = parse_concrete_okf_path(
        "01_docling_clean00/UPLOAD_DATA/reports/q1__v02.pdf.md"
    )
    assert parsed == ConcreteOkfPath(
        stage_folder="01_docling_clean00",
        raw=ConcreteRawPath(
            source="UPLOAD_DATA",
            directory="reports",
            stem="q1",
            extension=".pdf",
            version=2,
        ),
    )
    assert parsed.relative == "01_docling_clean00/UPLOAD_DATA/reports/q1__v02.pdf.md"
