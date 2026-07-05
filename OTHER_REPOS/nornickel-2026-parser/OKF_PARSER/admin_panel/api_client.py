"""HTTP client for parser API."""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.config.models import AppConfig
from app.presentation.schemas import StatisticsResponse


class ApiClient:
    def __init__(self, config: AppConfig) -> None:
        self._base_url = config.resolved_admin_api_base_url.rstrip("/")
        self._timeout = config.admin_panel.request_timeout_sec
        token = os.environ.get("ADMIN_PANEL_API_TOKEN", "").strip()
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    def get_statistics(self) -> StatisticsResponse:
        response = httpx.get(
            f"{self._base_url}/api/v1/statistics",
            timeout=self._timeout,
            headers=self._headers,
        )
        response.raise_for_status()
        return StatisticsResponse.model_validate(response.json())

    def post_reindex(self, *, enforce: bool = False) -> dict[str, Any]:
        response = httpx.post(
            f"{self._base_url}/api/v1/reindex",
            json={"enforce": enforce},
            timeout=self._timeout,
            headers=self._headers,
        )
        response.raise_for_status()
        return response.json()

    def health_check(self) -> bool:
        return self._check_any(("/health", "/api/v1/health"))

    def ready_check(self) -> bool:
        return self._check_any(("/ready", "/api/v1/ready"))

    def _check_any(self, paths: tuple[str, ...]) -> bool:
        for path in paths:
            try:
                response = httpx.get(
                    f"{self._base_url}{path}",
                    timeout=self._timeout,
                    headers=self._headers,
                )
                if response.status_code == 200:
                    return True
            except httpx.HTTPError:
                continue
        return False
