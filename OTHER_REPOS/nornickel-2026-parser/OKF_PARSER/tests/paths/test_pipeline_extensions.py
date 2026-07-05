"""Pipeline input extension helpers."""

from app.paths import is_archive_path, is_docling_input_path


def test_is_archive_path_detects_common_archives() -> None:
    assert is_archive_path("reports/bundle.zip")
    assert is_archive_path("RAW_DATA/data/archive.7z")
    assert is_archive_path("backup.tar.gz")


def test_is_docling_input_path_accepts_supported_documents() -> None:
    assert is_docling_input_path("reports/q1.pdf")
    assert is_docling_input_path("UPLOAD_DATA/reports/q1__v02.docx")
    assert is_docling_input_path("data/sheet.xlsx")


def test_is_docling_input_path_rejects_archives_and_legacy_spreadsheets() -> None:
    assert not is_docling_input_path("reports/bundle.zip")
    assert not is_docling_input_path("data/legacy.xls")
    assert not is_docling_input_path("notes.txt")
