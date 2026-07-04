"""Shared test fixtures for API and integration tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import fakeredis
import pytest
import yaml
from fastapi.testclient import TestClient


@pytest.fixture()
def shared_root(tmp_path: Path) -> Path:
    root = tmp_path / "SHARED"
    root.mkdir()
    for name in ("UPLOAD_DATA", "RAW_DATA", "00_docling_raw", "01_docling_clean00"):
        (root / name).mkdir()
    return root


@pytest.fixture()
def config_files(shared_root: Path, tmp_path: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "shared_root": str(shared_root),
                "queues": {
                    "raw2docling_raw": "parser:jobs:raw2docling_raw",
                    "docling_raw2docling_clean00": "parser:jobs:docling_raw2docling_clean00",
                },
                "api": {"host": "0.0.0.0", "port": 8114},
                "workers": {"raw2docling_raw": 1, "docling_raw2docling_clean00": 1},
                "locks": {
                    "upload_suffix": ".upload.lock",
                    "worker_suffix": ".worker.lock",
                },
                "pipeline": {"stages": ["docling_raw", "docling_clean00"]},
                "runtime": {"process_timeout_seconds": 600},
                "observability": {"metrics_enabled": True},
            }
        ),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text("REDIS_URL=redis://localhost:6379/0\n", encoding="utf-8")
    return config_path, env_path


@pytest.fixture()
def api_client(config_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch):
    config_path, env_path = config_files
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("ENV_PATH", str(env_path))
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    module_name = "service.main.main"
    if module_name in sys.modules:
        del sys.modules[module_name]

    with patch("service.main.main.redis.from_url", return_value=fake_redis):
        from service.main.main import app

        with TestClient(app) as client:
            yield client
