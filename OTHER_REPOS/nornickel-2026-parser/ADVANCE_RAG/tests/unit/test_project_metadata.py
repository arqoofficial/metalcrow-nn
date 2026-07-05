"""Project metadata and dependency declaration tests."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_readme_documents_uv_workflow() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "uv" in readme.lower() or "uv" in makefile


def test_pyproject_declares_core_runtime_dependencies() -> None:
    import tomllib

    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    dep_names = {d.split("[")[0].split(">=")[0].split("==")[0] for d in deps}
    required = {
        "fastapi",
        "uvicorn",
        "pydantic",
        "loguru",
        "chromadb",
        "fuzzysearch",
        "nltk",
        "prometheus-client",
        "opentelemetry-api",
        "opentelemetry-sdk",
    }
    missing = required - dep_names
    assert not missing, f"Missing runtime deps: {missing}"


def test_pyproject_declares_test_dependencies() -> None:
    import tomllib

    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev_deps = data["dependency-groups"]["dev"]
    dep_names = {d.split("[")[0].split(">=")[0].split("==")[0] for d in dev_deps}
    assert "pytest" in dep_names
    assert "pytest-asyncio" in dep_names
    assert "httpx" in dep_names


def test_uv_lock_exists() -> None:
    assert (PROJECT_ROOT / "uv.lock").is_file()
