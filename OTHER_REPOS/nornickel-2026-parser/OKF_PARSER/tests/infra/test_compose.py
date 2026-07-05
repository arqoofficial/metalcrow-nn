"""Step 09 - docker compose contract tests."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


def test_compose_has_required_core_services() -> None:
    services = _compose()["services"]
    for name in ("main", "raw2docling_raw", "docling_raw2docling_clean00", "redis"):
        assert name in services


def test_compose_profiles_defined() -> None:
    services = _compose()["services"]
    observability = [
        name
        for name, cfg in services.items()
        if cfg.get("profiles") == ["observability"]
    ]
    assert "prometheus" in observability
    assert "otel-collector" in observability


def test_worker_scaling_env_or_config_wired() -> None:
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "WORKERS_RAW2DOCLING_RAW" in text
    assert "WORKERS_DOCLING_RAW2DOCLING_CLEAN00" in text


def test_shared_volume_mounted_for_parser_services() -> None:
    services = _compose()["services"]
    for name in ("main", "raw2docling_raw", "docling_raw2docling_clean00"):
        mounts = services[name].get("volumes", [])
        assert any("./SHARED:/mnt/nfs/SHARED" in mount for mount in mounts)


def test_compose_uses_bundled_docker_config() -> None:
    docker_config = REPO_ROOT / "config" / "docker.yaml"
    assert docker_config.is_file()
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "config/docker.yaml" in dockerfile
    assert "pyproject.toml" in dockerfile
    assert "uv sync" in dockerfile
    assert "TORCH_VARIANT" in dockerfile
    assert "uv.lock.gpu" in dockerfile


def test_gpu_lockfile_present() -> None:
    gpu_lock = REPO_ROOT / "uv.lock.gpu"
    assert gpu_lock.is_file()
    text = gpu_lock.read_text(encoding="utf-8")
    assert 'name = "torch"' in text
    assert "nvidia-cublas" in text


def test_gpu_compose_overrides_torch_variant() -> None:
    gpu_compose = REPO_ROOT / "docker-compose.gpu.yml"
    text = gpu_compose.read_text(encoding="utf-8")
    assert "TORCH_VARIANT: gpu" in text
    assert "local-gpu" in text


def test_worker_services_have_model_cache_mount_and_env() -> None:
    services = _compose()["services"]
    for name in ("raw2docling_raw", "docling_raw2docling_clean00"):
        cfg = services[name]
        mounts = cfg.get("volumes", [])
        env = cfg.get("environment", {})
        assert any("./SHARED/MODELS:/models" in mount for mount in mounts)
        assert env.get("MODEL_CACHE_ROOT") == "/models"
        assert env.get("EASYOCR_MODULE_PATH") == "/models/easyocr"
        assert env.get("HF_HOME") == "/models/huggingface"
        assert env.get("REQUIRE_PRELOADED_MODELS") is not None
