"""Step 08 - admin panel CLI tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from admin_panel.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


def _write_config(tmp_path: Path, shared_root: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "shared_root": str(shared_root),
                "queues": {
                    "raw2docling_raw": "parser:jobs:raw2docling_raw",
                    "docling_raw2docling_clean00": "parser:jobs:docling_raw2docling_clean00",
                },
                "api": {"host": "127.0.0.1", "port": 8114},
                "workers": {"raw2docling_raw": 1, "docling_raw2docling_clean00": 1},
                "locks": {
                    "upload_suffix": ".upload.lock",
                    "worker_suffix": ".worker.lock",
                },
                "pipeline": {"stages": ["docling_raw", "docling_clean00"]},
                "runtime": {"process_timeout_seconds": 600},
                "admin_panel": {"api_base_url": "http://127.0.0.1:8114"},
            }
        ),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text("REDIS_URL=redis://localhost:6379/0\n", encoding="utf-8")
    return config_path, env_path


def test_panel_run_command_starts(tmp_path: Path, shared_root: Path, monkeypatch) -> None:
    config_path, env_path = _write_config(tmp_path, shared_root)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    with patch("admin_panel.refresh.refresh_state"), patch("admin_panel.main.time.sleep", return_value=None):
        result = runner.invoke(
            app,
            [
                "run",
                "--config",
                str(config_path),
                "--env-file",
                str(env_path),
                "--refresh-sec",
                "1",
                "--max-ticks",
                "1",
            ],
        )
    assert result.exit_code == 0


def test_panel_once_outputs_snapshot(tmp_path: Path, shared_root: Path, monkeypatch) -> None:
    config_path, env_path = _write_config(tmp_path, shared_root)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    with patch("admin_panel.api_client.ApiClient.get_statistics") as mock_stats:
        from app.presentation.schemas import StatisticsResponse

        mock_stats.return_value = StatisticsResponse(
            total_raw_files=1,
            stage0_done=0,
            stage1_done=0,
            coverage_ratio=0.0,
        )
        result = runner.invoke(
            app,
            ["once", "--config", str(config_path), "--env-file", str(env_path)],
        )
    assert result.exit_code == 0
    assert "Statistics" in result.stdout


def test_panel_errors_command(tmp_path: Path, shared_root: Path, monkeypatch) -> None:
    config_path, env_path = _write_config(tmp_path, shared_root)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    with patch("admin_panel.api_client.ApiClient.get_statistics", side_effect=RuntimeError("boom")):
        result = runner.invoke(
            app,
            ["errors", "--config", str(config_path), "--env-file", str(env_path)],
        )
    assert result.exit_code == 0
    assert "Recent Errors" in result.stdout


def test_panel_stats_command(tmp_path: Path, shared_root: Path, monkeypatch) -> None:
    config_path, env_path = _write_config(tmp_path, shared_root)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    with patch("admin_panel.api_client.ApiClient.get_statistics") as mock_stats:
        from app.presentation.schemas import StatisticsResponse

        mock_stats.return_value = StatisticsResponse(
            total_raw_files=3,
            stage0_done=1,
            stage1_done=1,
            coverage_ratio=0.33,
        )
        result = runner.invoke(
            app,
            ["stats", "--config", str(config_path), "--env-file", str(env_path)],
        )
    assert result.exit_code == 0
    assert "total_raw_files" in result.stdout


def test_panel_services_command(tmp_path: Path, shared_root: Path, monkeypatch) -> None:
    config_path, env_path = _write_config(tmp_path, shared_root)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    with patch("admin_panel.api_client.ApiClient.health_check", return_value=True):
        result = runner.invoke(
            app,
            ["services", "--config", str(config_path), "--env-file", str(env_path)],
        )
    assert result.exit_code == 0
    assert "service/main" in result.stdout


def test_panel_reindex_command_calls_api(tmp_path: Path, shared_root: Path, monkeypatch) -> None:
    config_path, env_path = _write_config(tmp_path, shared_root)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    with patch("admin_panel.api_client.ApiClient.post_reindex", return_value={"enqueued": 2}):
        result = runner.invoke(
            app,
            ["reindex", "--yes", "--config", str(config_path), "--env-file", str(env_path)],
        )
    assert result.exit_code == 0
    assert "enqueued=2" in result.stdout


def test_admin_panel_module_entrypoint_invokes_cli() -> None:
    result = subprocess.run(
        ["uv", "run", "-m", "admin_panel", "--help"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "run" in result.stdout
    assert "once" in result.stdout
