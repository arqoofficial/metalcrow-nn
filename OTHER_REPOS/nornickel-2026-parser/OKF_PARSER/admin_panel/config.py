"""Admin panel configuration loader."""

from __future__ import annotations

from pathlib import Path

from app.config.loader import load_config
from app.config.models import AppConfig


def load_panel_config(config_path: Path, env_path: Path) -> AppConfig:
    return load_config(config_path, env_path)
