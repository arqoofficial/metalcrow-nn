import os
import pytest
from unittest.mock import patch


def test_config_loads_defaults():
    with patch.dict(os.environ, {
        "REDIS_URL": "redis://localhost:6379/0",
        "MINIO_ENDPOINT": "localhost:9092",
        "MINIO_ACCESS_KEY": "minioadmin",
        "MINIO_SECRET_KEY": "minioadmin",
        "MINIO_BUCKET": "articles",
        "MINIO_PUBLIC_ENDPOINT": "http://localhost:9092",
    }):
        from app.config import Settings
        s = Settings()
        assert s.redis_url == "redis://localhost:6379/0"
        assert s.minio_bucket == "articles"


def test_stc_settings_default_off():
    """STC is opt-in: disabled by default, with inert defaults."""
    from app.config import Settings

    s = Settings()
    assert s.stc_enabled is False
    assert s.stc_index_alias == "nexus_science"
    assert s.stc_timeout == 60
    # ipfs_gateway_url has a sane default but is only used when stc_enabled is True
    assert isinstance(s.ipfs_gateway_url, str)


def test_stc_enabled_from_env(monkeypatch):
    monkeypatch.setenv("STC_ENABLED", "true")
    monkeypatch.setenv("IPFS_GATEWAY_URL", "http://ipfs:8080")
    from app.config import Settings

    s = Settings()
    assert s.stc_enabled is True
    assert s.ipfs_gateway_url == "http://ipfs:8080"
