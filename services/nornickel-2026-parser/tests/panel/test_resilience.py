"""Step 08 - panel resilience tests."""

from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from admin_panel.main import app

runner = CliRunner()


def test_widget_degradation_does_not_crash_panel(tmp_path: Path, shared_root: Path, monkeypatch) -> None:
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
                "locks": {"upload_suffix": ".upload.lock", "worker_suffix": ".worker.lock"},
                "pipeline": {"stages": ["docling_raw", "docling_clean00"]},
                "runtime": {"process_timeout_seconds": 600},
            }
        ),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text("REDIS_URL=redis://localhost:6379/0\n", encoding="utf-8")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    with patch("admin_panel.api_client.ApiClient.health_check", return_value=False), patch(
        "admin_panel.api_client.ApiClient.get_statistics",
        side_effect=RuntimeError("api down"),
    ):
        result = runner.invoke(
            app,
            ["once", "--config", str(config_path), "--env-file", str(env_path)],
        )
    assert result.exit_code == 0
    assert "Services" in result.stdout
