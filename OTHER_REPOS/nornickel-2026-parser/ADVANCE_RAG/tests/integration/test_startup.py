"""Startup integration tests."""

import os
from pathlib import Path

import fakeredis
import pytest
import yaml
from pydantic import ValidationError

from app.config.settings import clear_settings_cache, load_runtime_config
from app.main import create_app
from app.queue.redis_queue import RedisJobQueue


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_settings_cache()
    yield
    clear_settings_cache()


def _write_valid_config(tmp_path: Path, queue_backend: str = "memory") -> Path:
    data = {
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
        "queue": {"backend": queue_backend, "poll_interval_sec": 0.1},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


def test_startup_succeeds_with_valid_config(tmp_path: Path) -> None:
    path = _write_valid_config(tmp_path)
    app = create_app(path, tmp_path)
    assert app.title == "ADVANCE_RAG"
    assert app.state.app_state.runtime.api.version == "v1"


def test_startup_fails_with_invalid_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("query: {}\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_runtime_config(path, tmp_path)


def test_startup_fails_when_redis_selected_without_redis_url(tmp_path: Path) -> None:
    path = _write_valid_config(tmp_path, queue_backend="redis")
    os.environ.pop("REDIS_URL", None)
    with pytest.raises(ValueError, match="requires REDIS_URL"):
        create_app(path, tmp_path)


def test_startup_uses_redis_queue_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_valid_config(tmp_path, queue_backend="redis")
    fake_client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake_client)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    app = create_app(path, tmp_path)
    assert isinstance(app.state.app_state.queue, RedisJobQueue)


def test_startup_resolves_relative_shared_root_from_base_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_dir = tmp_path / "ADVANCE_RAG"
    base_dir.mkdir()
    shared_root = tmp_path / "SHARED"
    shared_root.mkdir()
    config_path = _write_valid_config(base_dir)
    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config_data["shared"]["root"] = "../SHARED"
    config_path.write_text(yaml.dump(config_data), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    app = create_app(config_path, base_dir)

    assert app.state.app_state.runtime.shared.resolve_root(base_dir) == shared_root.resolve()
