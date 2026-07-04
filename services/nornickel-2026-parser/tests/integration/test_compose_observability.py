"""Step 09 - compose observability integration tests."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _compose_config(*profiles: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if profiles:
        env["COMPOSE_PROFILES"] = ",".join(profiles)
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
        env=env,
    )


@pytest.mark.skipif(not _docker_available(), reason="docker not available")
def test_prometheus_scrapes_parser_metrics() -> None:
    result = _compose_config("observability")
    assert result.returncode == 0
    assert "prometheus:" in result.stdout
    assert "/metrics" in (REPO_ROOT / "infra/prometheus/prometheus.yml").read_text(encoding="utf-8")


@pytest.mark.skipif(not _docker_available(), reason="docker not available")
def test_otel_export_pipeline_non_blocking() -> None:
    result = _compose_config("observability")
    assert result.returncode == 0
    assert "otel-collector:" in result.stdout


def test_langfuse_disabled_by_default_in_runtime() -> None:
    from app.config.models import ObservabilityConfig

    assert ObservabilityConfig().langfuse_enabled is False
