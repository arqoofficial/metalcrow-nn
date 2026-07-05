"""Typed configuration models and settings loader."""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChromaMode(str, Enum):
    CPU_LOCAL = "cpu_local"
    OPENAPI = "openapi"


class QueryPreprocessingConfig(BaseModel):
    lemmatization: bool = True
    stemming: bool = True
    languages: list[str] = Field(default_factory=lambda: ["en", "ru"])


class QueryConfig(BaseModel):
    default_type: str = "dense"
    default_limit: int = 10
    default_source_subfolder: str = "01_docling_clean00"
    allowed_source_subfolders: list[str] = Field(
        default_factory=lambda: ["00_docling_raw", "01_docling_clean00"]
    )
    preprocessing: QueryPreprocessingConfig = Field(default_factory=QueryPreprocessingConfig)

    @field_validator("allowed_source_subfolders")
    @classmethod
    def validate_allowed_subfolders(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("allowed_source_subfolders must not be empty")
        return value


class ApiConfig(BaseModel):
    version: str = "v1"
    host: str = "0.0.0.0"
    port: int = 8115


class SharedConfig(BaseModel):
    root: str

    def resolve_root(self, base_dir: Path | None = None) -> Path:
        root_path = Path(self.root)
        if root_path.is_absolute():
            return root_path.resolve()
        base = base_dir or Path.cwd()
        return (base / root_path).resolve()


class ChromaOpenApiConfig(BaseModel):
    api_key_env: str = "CHROMA_OPENAI_API_KEY"
    base_url_env: str = "CHROMA_OPENAI_BASE_URL"


class ChromaConfig(BaseModel):
    mode: ChromaMode = ChromaMode.CPU_LOCAL
    persist_directory: str = "./data/chroma"
    collection_name: str = "advance_rag"
    openapi: ChromaOpenApiConfig = Field(default_factory=ChromaOpenApiConfig)


class QueueConfig(BaseModel):
    backend: Literal["memory", "redis"] = "memory"
    poll_interval_sec: float = 1.0
    redis_url_env: str = "REDIS_URL"


class ObservabilityConfig(BaseModel):
    metrics_enabled: bool = True
    tracing_enabled: bool = True
    log_json: bool = True


class McpConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8120


class RuntimeConfig(BaseModel):
    api: ApiConfig
    shared: SharedConfig
    query: QueryConfig
    chroma: ChromaConfig
    queue: QueueConfig = Field(default_factory=QueueConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)


class SecretsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    chroma_openai_api_key: str | None = None
    chroma_openai_base_url: str | None = None
    otel_exporter_otlp_endpoint: str | None = None
    redis_url: str | None = None
    queue_backend: Literal["memory", "redis"] | None = Field(
        default=None,
        validation_alias=AliasChoices("QUEUE_BACKEND"),
    )


def apply_env_overrides(runtime: RuntimeConfig, secrets: SecretsSettings) -> RuntimeConfig:
    if secrets.queue_backend is None:
        return runtime
    return runtime.model_copy(
        update={"queue": runtime.queue.model_copy(update={"backend": secrets.queue_backend})}
    )


def _config_paths(base_dir: Path) -> tuple[Path, Path]:
    return base_dir / "config.yaml", base_dir / ".env"


def load_runtime_config(
    config_path: Path | None = None,
    base_dir: Path | None = None,
) -> RuntimeConfig:
    base = base_dir or Path(__file__).resolve().parents[2]
    path = config_path or base / "config.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError("config.yaml must contain a mapping")
    return RuntimeConfig.model_validate(raw)


@lru_cache
def get_settings(
    config_path: str | None = None,
    base_dir: str | None = None,
) -> tuple[RuntimeConfig, SecretsSettings]:
    base = Path(base_dir) if base_dir else Path(__file__).resolve().parents[2]
    cfg_path = Path(config_path) if config_path else base / "config.yaml"
    env_path = base / ".env"
    if env_path.is_file():
        os.environ.setdefault("DOTENV_PATH", str(env_path))
    runtime = load_runtime_config(cfg_path, base)
    if env_path.is_file():
        secrets = SecretsSettings(_env_file=str(env_path))  # type: ignore[call-arg]
    else:
        secrets = SecretsSettings(_env_file=None)
    runtime = apply_env_overrides(runtime, secrets)
    return runtime, secrets


def clear_settings_cache() -> None:
    get_settings.cache_clear()
