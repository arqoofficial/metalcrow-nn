"""Step 09 - compose stack integration tests."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _compose_config() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )


@pytest.mark.skipif(not _docker_available(), reason="docker not available")
def test_compose_up_core_stack() -> None:
    result = _compose_config()
    assert result.returncode == 0
    assert "main:" in result.stdout
    assert "redis:" in result.stdout


@pytest.mark.skipif(not _docker_available(), reason="docker not available")
def test_api_and_workers_reach_shared_storage() -> None:
    result = _compose_config()
    assert result.returncode == 0
    assert "/mnt/nfs/SHARED" in result.stdout
    assert "../SHARED" in str(COMPOSE_FILE.read_text(encoding="utf-8"))


@pytest.mark.skipif(not _docker_available(), reason="docker not available")
def test_redis_queue_connectivity_in_compose() -> None:
    result = _compose_config()
    assert result.returncode == 0
    assert "REDIS_URL" in result.stdout
