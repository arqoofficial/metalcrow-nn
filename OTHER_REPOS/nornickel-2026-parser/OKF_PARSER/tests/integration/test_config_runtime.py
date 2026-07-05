"""Step 01 - configuration runtime integration tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import fakeredis
import pytest
import yaml
from fastapi.testclient import TestClient

from app.config.loader import ConfigError, load_config, log_worker_counts
from app.queue.redis_queue import MAIN_LEADER_KEY

REPO_ROOT = Path(__file__).resolve().parents[2]


def _valid_config_dict(shared_root: str) -> dict:
    return {
        "shared_root": shared_root,
        "queues": {
            "raw2docling_raw": "parser:jobs:raw2docling_raw",
            "docling_raw2docling_clean00": "parser:jobs:docling_raw2docling_clean00",
        },
        "api": {"host": "0.0.0.0", "port": 8114},
        "workers": {"raw2docling_raw": 2, "docling_raw2docling_clean00": 3},
        "locks": {"upload_suffix": ".upload.lock", "worker_suffix": ".worker.lock"},
        "pipeline": {"stages": ["docling_raw", "docling_clean00"]},
        "runtime": {"process_timeout_seconds": 600},
    }


def _write_runtime_files(tmp_path: Path) -> tuple[Path, Path]:
    shared_root = tmp_path / "SHARED"
    shared_root.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.dump(_valid_config_dict(str(shared_root))), encoding="utf-8"
    )
    env_path = tmp_path / ".env"
    env_path.write_text("REDIS_URL=redis://localhost:6379/0\n", encoding="utf-8")
    return config_path, env_path


def test_main_service_boot_with_config_and_env(tmp_path: Path, monkeypatch) -> None:
    config_path, env_path = _write_runtime_files(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("ENV_PATH", str(env_path))

    fake_redis = fakeredis.FakeRedis(decode_responses=False)

    with patch("service.main.main.redis.from_url", return_value=fake_redis):
        if "service.main.main" in sys.modules:
            del sys.modules["service.main.main"]
        from service.main import main as main_module

        with TestClient(main_module.app) as client:
            assert client.app.state.config.workers.raw2docling_raw == 2
            assert fake_redis.get(MAIN_LEADER_KEY) is not None


def test_worker_boot_with_config_and_env(tmp_path: Path, monkeypatch) -> None:
    config_path, env_path = _write_runtime_files(tmp_path)

    raw_config = load_config(config_path, env_path)
    clean_config = load_config(config_path, env_path)

    assert raw_config.workers.raw2docling_raw == 2
    assert clean_config.workers.docling_raw2docling_clean00 == 3

    captured: list[str] = []

    def fake_print(message: str, *args: object, **kwargs: object) -> None:
        captured.append(message)

    with patch("builtins.print", fake_print):
        log_worker_counts(raw_config)
        log_worker_counts(clean_config)

    assert any("raw2docling_raw=2" in line for line in captured)
    assert any("docling_raw2docling_clean00=3" in line for line in captured)


def test_invalid_workers_config_blocks_boot(tmp_path: Path) -> None:
    shared_root = tmp_path / "SHARED"
    shared_root.mkdir()
    data = _valid_config_dict(str(shared_root))
    data["workers"]["raw2docling_raw"] = 0
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(config_path, tmp_path / "missing.env")
