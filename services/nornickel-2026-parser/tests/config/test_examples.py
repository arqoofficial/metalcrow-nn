"""Step 01 - example config files parse successfully."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.config.loader import load_config
from app.config.models import AppConfig

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_examples_parse(tmp_path, monkeypatch) -> None:
    shared_root = tmp_path / "SHARED"
    shared_root.mkdir()
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    example_path = REPO_ROOT / "config.yaml.example"
    raw = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    raw["shared_root"] = str(shared_root)
    AppConfig.model_validate(raw)

    env_example = REPO_ROOT / ".env.example"
    assert env_example.is_file()
    env_path = tmp_path / ".env"
    env_path.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(raw), encoding="utf-8")

    config = load_config(config_path, env_path)
    assert config.api.port == 8114
    assert config.workers.raw2docling_raw == 4
