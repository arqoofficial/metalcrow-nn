"""Step 09 - Dockerfile presence tests."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_DOCKERFILES = [
    REPO_ROOT / "service/main/Dockerfile",
    REPO_ROOT / "service/raw2docling_raw/Dockerfile",
    REPO_ROOT / "service/docling_raw2docling_clean00/Dockerfile",
]


def test_uv_project_files_exist() -> None:
    assert (REPO_ROOT / "pyproject.toml").is_file()
    assert (REPO_ROOT / "uv.lock").is_file()


def test_all_required_service_dockerfiles_exist() -> None:
    missing = [str(path) for path in REQUIRED_DOCKERFILES if not path.is_file()]
    assert not missing, f"Missing Dockerfiles: {missing}"
