"""Config loader unit tests."""

from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from app.config.settings import (
    RuntimeConfig,
    SecretsSettings,
    apply_env_overrides,
    clear_settings_cache,
    get_settings,
    load_runtime_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_settings_cache()
    yield
    clear_settings_cache()


def _write_config(tmp_path: Path, overrides: dict | None = None) -> Path:
    base = {
        "api": {"version": "v1", "host": "0.0.0.0", "port": 8114},
        "shared": {"root": str(tmp_path / "SHARED")},
        "query": {
            "default_type": "advance",
            "default_limit": 10,
            "default_source_subfolder": "01_docling_clean00",
            "allowed_source_subfolders": ["00_docling_raw", "01_docling_clean00"],
            "preprocessing": {"lemmatization": True, "stemming": True, "languages": ["en", "ru"]},
        },
        "chroma": {
            "mode": "cpu_local",
            "persist_directory": str(tmp_path / "chroma"),
            "collection_name": "test",
        },
    }
    if overrides:
        base.update(overrides)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(base), encoding="utf-8")
    return path


def test_valid_config_loads(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    cfg = load_runtime_config(path, tmp_path)
    assert cfg.query.default_type == "advance"
    assert cfg.query.default_limit == 10
    assert cfg.query.default_source_subfolder == "01_docling_clean00"
    assert "01_docling_clean00" in cfg.query.allowed_source_subfolders


def test_missing_required_fields_fail_fast(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("api:\n  version: v1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_runtime_config(path, tmp_path)


def test_allowed_subfolder_list_must_not_be_empty(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["query"]["allowed_source_subfolders"] = []
    path.write_text(yaml.dump(data), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_runtime_config(path, tmp_path)


def test_get_settings_entrypoint(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    runtime, secrets = get_settings(str(path), str(tmp_path))
    assert isinstance(runtime, RuntimeConfig)
    assert isinstance(secrets, SecretsSettings)


def test_config_models_are_pydantic_basemodel() -> None:
    from app.config.settings import QueryConfig, RuntimeConfig, SharedConfig

    assert issubclass(RuntimeConfig, BaseModel)
    assert issubclass(QueryConfig, BaseModel)
    assert issubclass(SharedConfig, BaseModel)


def test_project_config_yaml_loads() -> None:
    cfg = load_runtime_config(PROJECT_ROOT / "config.yaml", PROJECT_ROOT)
    assert cfg.shared.root
    assert cfg.query.default_source_subfolder == "01_docling_clean00"


def test_queue_backend_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "QUEUE_BACKEND=redis\nREDIS_URL=redis://localhost:6379/0\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    runtime, secrets = get_settings(str(path), str(tmp_path))
    assert runtime.queue.backend == "redis"
    assert secrets.redis_url == "redis://localhost:6379/0"


def test_apply_env_overrides_noop_without_queue_backend(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    runtime = load_runtime_config(path, tmp_path)
    secrets = SecretsSettings(_env_file=None)
    assert apply_env_overrides(runtime, secrets).queue.backend == "memory"
