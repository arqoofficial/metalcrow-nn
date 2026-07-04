"""Step 02 - exact path resolution."""

from pathlib import Path

from app.services.path_resolution import list_raw_concrete_paths, resolve_exact_raw_path


def test_logical_path_prefers_upload_over_raw(tmp_path: Path) -> None:
    shared = tmp_path / "SHARED"
    upload = shared / "UPLOAD_DATA" / "reports" / "q1.pdf"
    raw = shared / "RAW_DATA" / "reports" / "q1.pdf"
    upload.parent.mkdir(parents=True, exist_ok=True)
    raw.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"upload")
    raw.write_bytes(b"raw")

    resolved = resolve_exact_raw_path("reports/q1.pdf", str(shared))
    assert resolved is not None
    assert resolved.relative == "UPLOAD_DATA/reports/q1.pdf"


def test_exact_concrete_path_only(tmp_path: Path) -> None:
    shared = tmp_path / "SHARED"
    v1 = shared / "UPLOAD_DATA" / "reports" / "q1__v01.pdf"
    v3 = shared / "UPLOAD_DATA" / "reports" / "q1__v03.pdf"
    v1.parent.mkdir(parents=True)
    v1.write_bytes(b"v1")
    v3.write_bytes(b"v3")

    missing = resolve_exact_raw_path("UPLOAD_DATA/reports/q1__v02.pdf", str(shared))
    assert missing is None

    exact = resolve_exact_raw_path("UPLOAD_DATA/reports/q1__v03.pdf", str(shared))
    assert exact is not None
    assert exact.version == 3


def test_list_raw_concrete_paths_skips_archives_and_unsupported_types(tmp_path: Path) -> None:
    shared = tmp_path / "SHARED"
    targets = [
        "RAW_DATA/reports/q1.pdf",
        "RAW_DATA/archives/bundle.zip",
        "RAW_DATA/data/legacy.xls",
        "UPLOAD_DATA/reports/q2.docx",
    ]
    for relative in targets:
        path = shared / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    listed = list_raw_concrete_paths(str(shared))
    assert set(listed) == {"RAW_DATA/reports/q1.pdf", "UPLOAD_DATA/reports/q2.docx"}


def test_resolve_exact_raw_path_returns_none_for_archive(tmp_path: Path) -> None:
    shared = tmp_path / "SHARED"
    archive = shared / "RAW_DATA" / "bundle.zip"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"PK")

    assert resolve_exact_raw_path("RAW_DATA/bundle.zip", str(shared)) is None
    assert resolve_exact_raw_path("bundle.zip", str(shared)) is None
