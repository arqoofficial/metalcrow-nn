"""Load and validate config.yaml with .env secrets and interpolation."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from app.config.models import AppConfig

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")
_FORBIDDEN_KEY_FRAGMENTS = ("password", "secret", "token", "api_key")


class ConfigError(Exception):
    """Configuration load or validation failure."""


def _reject_secret_keys(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_lower = key.lower()
            if any(fragment in key_lower for fragment in _FORBIDDEN_KEY_FRAGMENTS):
                location = f"{path}.{key}" if path else key
                raise ConfigError(
                    f"Secret-like key not allowed in config.yaml: {location}"
                )
            child_path = f"{path}.{key}" if path else key
            _reject_secret_keys(value, child_path)
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            _reject_secret_keys(item, f"{path}[{index}]")


def _interpolate(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _interpolate_str(value, env)
    if isinstance(value, dict):
        return {key: _interpolate(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item, env) for item in value]
    return value


def _interpolate_str(value: str, env: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name not in env:
            raise ConfigError(f"Environment variable not set: {var_name}")
        return env[var_name]

    return _ENV_VAR_PATTERN.sub(replace, value)


def load_config(
    config_path: str | Path = "config.yaml",
    env_path: str | Path = ".env",
) -> AppConfig:
    """Load config.yaml, .env, interpolate env vars, validate, return AppConfig."""
    config_file = Path(config_path)
    env_file = Path(env_path)

    if env_file.is_file():
        load_dotenv(env_file)

    if not config_file.is_file():
        raise ConfigError(f"Config file not found: {config_file}")

    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml must contain a mapping at the top level")

    _reject_secret_keys(raw)
    interpolated = _interpolate(raw, dict(os.environ))

    try:
        return AppConfig.model_validate(interpolated)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def log_worker_counts(config: AppConfig) -> None:
    print(
        "Effective worker counts: "
        f"raw2docling_raw={config.workers.raw2docling_raw}, "
        f"docling_raw2docling_clean00={config.workers.docling_raw2docling_clean00}"
    )
