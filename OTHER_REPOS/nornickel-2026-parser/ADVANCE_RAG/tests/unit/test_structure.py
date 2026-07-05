"""Structure smoke test for ADVANCE_RAG module layout."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_DIRECTORIES = [
    "app",
    "app/api",
    "app/config",
    "app/data",
    "app/retrieval",
    "app/indexing",
    "app/queue",
    "app/observability",
    "tests/unit",
    "tests/integration",
]


def test_required_directories_exist() -> None:
    for relative in REQUIRED_DIRECTORIES:
        path = PROJECT_ROOT / relative
        assert path.is_dir(), f"Missing required directory: {relative}"


def test_app_packages_importable() -> None:
    import app  # noqa: F401
    import app.api  # noqa: F401
    import app.config  # noqa: F401
    import app.data  # noqa: F401
    import app.indexing  # noqa: F401
    import app.observability  # noqa: F401
    import app.queue  # noqa: F401
    import app.retrieval  # noqa: F401
