"""Pydantic configuration models."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class QueuesConfig(BaseModel):
    model_config = ConfigDict(strict=True)

    raw2docling_raw: str
    docling_raw2docling_clean00: str

    @field_validator("raw2docling_raw", "docling_raw2docling_clean00")
    @classmethod
    def queue_names_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("queue name must be non-empty")
        return value


class ApiConfig(BaseModel):
    model_config = ConfigDict(strict=True)

    host: str
    port: int


class WorkersConfig(BaseModel):
    model_config = ConfigDict(strict=True)

    raw2docling_raw: int = Field(ge=1)
    docling_raw2docling_clean00: int = Field(ge=1)


class LocksConfig(BaseModel):
    model_config = ConfigDict(strict=True)

    upload_suffix: str
    worker_suffix: str


class DoclingConfig(BaseModel):
    model_config = ConfigDict(strict=True)

    ocr_enabled: bool = True
    ocr_languages: list[str] = Field(default_factory=lambda: ["en", "ru"])

    @field_validator("ocr_languages")
    @classmethod
    def validate_ocr_languages(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().lower() for item in value if item.strip()]
        if not normalized:
            raise ValueError("ocr_languages must contain at least one language code")
        return normalized


class PipelineConfig(BaseModel):
    model_config = ConfigDict(strict=True)

    stages: list[str]
    docling: DoclingConfig = Field(default_factory=DoclingConfig)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(strict=True)

    process_timeout_seconds: int
    redis_retry_attempts: int = 3
    lock_retry_attempts: int = 3
    retry_base_delay_seconds: float = 0.1


class AdminPanelActionsConfig(BaseModel):
    model_config = ConfigDict(strict=True)

    allow_reindex: bool = True
    allow_restart_hooks: bool = False
    restart_hooks_script: str = "rerun.sh"
    confirm_destructive_actions: bool = True


class AdminPanelConfig(BaseModel):
    model_config = ConfigDict(strict=True)

    enabled: bool = True
    refresh_sec: int = 3
    api_base_url: str | None = None
    error_buffer_size: int = 50
    request_timeout_sec: int = 5
    show_lock_files: bool = True
    actions: AdminPanelActionsConfig = Field(default_factory=AdminPanelActionsConfig)


class ObservabilityConfig(BaseModel):
    model_config = ConfigDict(strict=True)

    metrics_enabled: bool = False
    otel_enabled: bool = False
    langfuse_enabled: bool = False


class AppConfig(BaseModel):
    model_config = ConfigDict(strict=True)

    shared_root: str
    queues: QueuesConfig
    api: ApiConfig
    workers: WorkersConfig
    locks: LocksConfig
    pipeline: PipelineConfig
    runtime: RuntimeConfig
    admin_panel: AdminPanelConfig = Field(default_factory=AdminPanelConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @field_validator("shared_root")
    @classmethod
    def validate_shared_root(cls, value: str) -> str:
        path = Path(value)
        if not path.exists():
            raise ValueError(f"shared_root does not exist: {value}")
        if not os.access(path, os.W_OK):
            raise ValueError(f"shared_root is not writable: {value}")
        return value

    @model_validator(mode="after")
    def validate_admin_api_base_url(self) -> AppConfig:
        if self.admin_panel.api_base_url is not None and not self.admin_panel.api_base_url.strip():
            raise ValueError("admin_panel.api_base_url must be non-empty when set")
        return self

    @property
    def resolved_admin_api_base_url(self) -> str:
        if self.admin_panel.api_base_url:
            return self.admin_panel.api_base_url
        return f"http://{self.api.host}:{self.api.port}"
