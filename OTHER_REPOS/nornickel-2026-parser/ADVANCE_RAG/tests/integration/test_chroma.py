"""Chroma adapter integration tests."""

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from app.config.settings import ChromaMode, RuntimeConfig, SecretsSettings, load_runtime_config
from app.data.chroma_adapter import ChromaAdapter, create_chroma_adapter


def _config(tmp_path: Path) -> RuntimeConfig:
    data = {
        "api": {"version": "v1", "host": "0.0.0.0", "port": 8114},
        "shared": {"root": str(tmp_path / "SHARED")},
        "query": {
            "default_type": "advance",
            "default_limit": 10,
            "default_source_subfolder": "01_docling_clean00",
            "allowed_source_subfolders": ["01_docling_clean00"],
            "preprocessing": {"lemmatization": False, "stemming": False, "languages": ["en"]},
        },
        "chroma": {
            "mode": "cpu_local",
            "persist_directory": str(tmp_path / "chroma"),
            "collection_name": f"itest_{tmp_path.name}",
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return load_runtime_config(path, tmp_path)


def test_collection_initialize_and_upsert_query(tmp_path: Path) -> None:
    runtime = _config(tmp_path)
    adapter = create_chroma_adapter(runtime, tmp_path)
    assert adapter.is_ready
    adapter.upsert(
        "doc-1",
        "nickel production forecast growth",
        {"path": "reports/q1.okf.md", "source_subfolder": "01_docling_clean00"},
    )
    results = adapter.query_dense("nickel forecast", limit=5)
    assert results
    assert results[0]["id"] == "doc-1"


def test_unavailable_chroma_dependency_surfaces_error(tmp_path: Path) -> None:
    runtime = _config(tmp_path)
    adapter = ChromaAdapter(runtime.chroma, tmp_path)
    with pytest.raises(RuntimeError, match="not initialized"):
        adapter.query_dense("test", limit=1)


def test_delete_collection_returns_false_on_error(tmp_path: Path) -> None:
    runtime = _config(tmp_path)
    adapter = ChromaAdapter(runtime.chroma, tmp_path)
    mock_client = Mock()
    mock_client.delete_collection.side_effect = RuntimeError("cannot delete")
    adapter._client = mock_client
    assert adapter.delete_collection() is False


def test_delete_collection_returns_true_on_success(tmp_path: Path) -> None:
    runtime = _config(tmp_path)
    adapter = create_chroma_adapter(runtime, tmp_path)
    assert adapter.delete_collection() is True


def test_openapi_mode_fails_fast_without_secrets(tmp_path: Path) -> None:
    runtime = _config(tmp_path)
    runtime.chroma.mode = ChromaMode.OPENAPI
    with pytest.raises(ValueError, match="requires CHROMA_OPENAI_API_KEY"):
        create_chroma_adapter(runtime, tmp_path, secrets=SecretsSettings())


def test_openapi_mode_initializes_with_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _config(tmp_path)
    runtime.chroma.mode = ChromaMode.OPENAPI

    class DummyEmbedding:
        def __call__(self, input):  # noqa: A002
            return [[0.0] * 3 for _ in input]

    monkeypatch.setattr(
        "app.data.chroma_adapter._build_openapi_embedding",
        lambda api_key, api_base: DummyEmbedding(),
    )
    secrets = SecretsSettings(
        chroma_openai_api_key="key",
        chroma_openai_base_url="https://example.local/v1",
    )
    adapter = create_chroma_adapter(runtime, tmp_path, secrets=secrets)
    assert adapter.is_ready
