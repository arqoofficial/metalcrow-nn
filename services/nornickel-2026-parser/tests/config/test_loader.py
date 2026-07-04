"""Step 01 - configuration loader tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from app.config.loader import ConfigError, load_config
from app.config.models import AppConfig

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_config(tmp_path: Path, data: dict) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(data), encoding="utf-8")
    return config_path


def _valid_config_dict(shared_root: str) -> dict:
    return {
        "shared_root": shared_root,
        "queues": {
            "raw2docling_raw": "parser:jobs:raw2docling_raw",
            "docling_raw2docling_clean00": "parser:jobs:docling_raw2docling_clean00",
        },
        "api": {"host": "0.0.0.0", "port": 8114},
        "workers": {"raw2docling_raw": 2, "docling_raw2docling_clean00": 4},
        "locks": {"upload_suffix": ".upload.lock", "worker_suffix": ".worker.lock"},
        "pipeline": {"stages": ["docling_raw", "docling_clean00"]},
        "runtime": {"process_timeout_seconds": 600},
    }


def test_load_valid_config_and_env(tmp_path: Path) -> None:
    shared_root = tmp_path / "SHARED"
    shared_root.mkdir()
    config_path = _write_config(tmp_path, _valid_config_dict(str(shared_root)))
    env_path = tmp_path / ".env"
    env_path.write_text("REDIS_URL=redis://localhost:6379/0\n", encoding="utf-8")

    config = load_config(config_path, env_path)

    assert isinstance(config, AppConfig)
    assert config.shared_root == str(shared_root)
    assert config.workers.raw2docling_raw == 2
    assert config.pipeline.docling.ocr_languages == ["en", "ru"]
    assert os.environ["REDIS_URL"] == "redis://localhost:6379/0"


def test_env_interpolation_in_yaml(tmp_path: Path) -> None:
    shared_root = tmp_path / "SHARED"
    shared_root.mkdir()
    data = _valid_config_dict("${SHARED_ROOT_PATH}")
    config_path = _write_config(tmp_path, data)
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"SHARED_ROOT_PATH={shared_root}\nREDIS_URL=redis://localhost:6379/0\n",
        encoding="utf-8",
    )

    config = load_config(config_path, env_path)

    assert config.shared_root == str(shared_root)


def test_missing_workers_block_startup(tmp_path: Path) -> None:
    shared_root = tmp_path / "SHARED"
    shared_root.mkdir()
    data = _valid_config_dict(str(shared_root))
    del data["workers"]
    config_path = _write_config(tmp_path, data)

    with pytest.raises(ConfigError):
        load_config(config_path, tmp_path / "missing.env")


@pytest.mark.parametrize(
    "workers",
    [
        {"raw2docling_raw": 0, "docling_raw2docling_clean00": 1},
        {"raw2docling_raw": -1, "docling_raw2docling_clean00": 1},
        {"raw2docling_raw": 1.5, "docling_raw2docling_clean00": 1},
        {"raw2docling_raw": "2", "docling_raw2docling_clean00": 1},
    ],
)
def test_worker_count_must_be_positive_int(
    tmp_path: Path, workers: dict[str, object]
) -> None:
    shared_root = tmp_path / "SHARED"
    shared_root.mkdir()
    data = _valid_config_dict(str(shared_root))
    data["workers"] = workers
    config_path = _write_config(tmp_path, data)

    with pytest.raises(ConfigError):
        load_config(config_path, tmp_path / "missing.env")


def test_config_rejects_secret_fields(tmp_path: Path) -> None:
    shared_root = tmp_path / "SHARED"
    shared_root.mkdir()
    data = _valid_config_dict(str(shared_root))
    data["redis_password"] = "secret-value"
    config_path = _write_config(tmp_path, data)

    with pytest.raises(ConfigError, match="Secret-like key"):
        load_config(config_path, tmp_path / "missing.env")


def test_shared_root_must_exist_and_be_writable(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing"
    data = _valid_config_dict(str(missing_root))
    config_path = _write_config(tmp_path, data)

    with pytest.raises(ConfigError, match="shared_root does not exist"):
        load_config(config_path, tmp_path / "missing.env")

    read_only_root = tmp_path / "readonly"
    read_only_root.mkdir()
    read_only_root.chmod(0o555)
    try:
        readonly_case = tmp_path / "readonly_case"
        readonly_case.mkdir()
        data = _valid_config_dict(str(read_only_root))
        config_path = _write_config(readonly_case, data)
        with pytest.raises(ConfigError, match="shared_root is not writable"):
            load_config(config_path, tmp_path / "missing.env")
    finally:
        read_only_root.chmod(0o755)
