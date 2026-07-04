"""Step 08 - admin panel runtime integration tests."""

from __future__ import annotations

from unittest.mock import patch

import httpx
from typer.testing import CliRunner

from admin_panel import api_client as api_client_module
from admin_panel.main import app

runner = CliRunner()


class _HttpxAdapter:
    def __init__(self, test_client) -> None:
        self._test_client = test_client

    def get(self, url: str, **kwargs):
        path = url.split("/api/v1", 1)[-1]
        response = self._test_client.get(f"/api/v1{path}")
        return _wrap_response(response)

    def post(self, url: str, **kwargs):
        path = url.split("/api/v1", 1)[-1]
        response = self._test_client.post(f"/api/v1{path}", json=kwargs.get("json"))
        return _wrap_response(response)


class _Wrap:
    def __init__(self, response) -> None:
        self.status_code = response.status_code
        self._response = response

    def json(self):
        return self._response.json()

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "request failed",
                request=httpx.Request("GET", "http://test"),
                response=httpx.Response(self.status_code),
            )


def _wrap_response(response):
    return _Wrap(response)


def _patch_httpx(api_client, monkeypatch) -> None:
    adapter = _HttpxAdapter(api_client)
    monkeypatch.setattr(api_client_module.httpx, "get", adapter.get)
    monkeypatch.setattr(api_client_module.httpx, "post", adapter.post)


def test_panel_sh_live_run_boots_with_real_api(api_client, shared_root: Path, config_files, monkeypatch) -> None:
    config_path, env_path = config_files
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    with patch("admin_panel.main.time.sleep", return_value=None):
        result = runner.invoke(
            app,
            [
                "run",
                "--config",
                str(config_path),
                "--env-file",
                str(env_path),
                "--max-ticks",
                "1",
            ],
        )
    assert result.exit_code == 0


def test_panel_services_widget_with_running_workers(api_client, config_files, monkeypatch) -> None:
    config_path, env_path = config_files
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    result = runner.invoke(
        app,
        ["services", "--config", str(config_path), "--env-file", str(env_path)],
    )
    assert result.exit_code == 0
    assert "raw2docling_raw" in result.stdout


def test_panel_stats_widget_uses_statistics_endpoint(api_client, shared_root: Path, config_files, monkeypatch) -> None:
    config_path, env_path = config_files
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    _patch_httpx(api_client, monkeypatch)
    (shared_root / "UPLOAD_DATA" / "reports").mkdir(parents=True)
    (shared_root / "UPLOAD_DATA" / "reports" / "q1__v01.pdf").write_bytes(b"raw")
    result = runner.invoke(
        app,
        ["stats", "--config", str(config_path), "--env-file", str(env_path)],
    )
    assert result.exit_code == 0
    assert "total_raw_files" in result.stdout


def test_panel_reindex_action_hits_reindex_endpoint(api_client, shared_root: Path, config_files, monkeypatch) -> None:
    config_path, env_path = config_files
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    _patch_httpx(api_client, monkeypatch)
    (shared_root / "UPLOAD_DATA" / "reports").mkdir(parents=True)
    (shared_root / "UPLOAD_DATA" / "reports" / "q1__v01.pdf").write_bytes(b"raw")
    result = runner.invoke(
        app,
        ["reindex", "--yes", "--config", str(config_path), "--env-file", str(env_path)],
    )
    assert result.exit_code == 0
    assert "enqueued=" in result.stdout


def test_panel_survives_single_data_source_failure(api_client, config_files, monkeypatch) -> None:
    config_path, env_path = config_files
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    with patch("admin_panel.api_client.ApiClient.get_statistics", side_effect=RuntimeError("stats down")):
        result = runner.invoke(
            app,
            ["once", "--config", str(config_path), "--env-file", str(env_path)],
        )
    assert result.exit_code == 0
