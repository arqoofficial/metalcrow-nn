"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config.settings import clear_settings_cache
from app.main import create_app


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def shared_tree(tmp_path: Path) -> Path:
    shared = tmp_path / "SHARED"
    for sub in ("00_docling_raw", "01_docling_clean00"):
        (shared / sub / "reports").mkdir(parents=True)
    sample = """---
type: report
title: Nickel Production Forecast
description: Q1 outlook
resource: okf://reports/q1
tags:
  - nickel
  - forecast
timestamp: "2026-01-15T10:00:00Z"
---

Nickel production forecast for the first quarter shows steady growth.
"""
    (shared / "01_docling_clean00" / "reports" / "q1_report.okf.md").write_text(
        sample, encoding="utf-8"
    )
    russian = """---
type: report
title: Прогноз производства никеля
description: Краткий обзор
---

Прогноз производства никеля на первый квартал показывает рост.
"""
    (shared / "01_docling_clean00" / "reports" / "ru_report.okf.md").write_text(
        russian, encoding="utf-8"
    )
    return shared


def write_test_config(tmp_path: Path, shared: Path, collection: str = "test_collection") -> Path:
    data = {
        "api": {"version": "v1", "host": "0.0.0.0", "port": 8114},
        "shared": {"root": str(shared)},
        "query": {
            "default_type": "advance",
            "default_limit": 10,
            "default_source_subfolder": "01_docling_clean00",
            "allowed_source_subfolders": ["00_docling_raw", "01_docling_clean00"],
            "preprocessing": {"lemmatization": True, "stemming": True, "languages": ["en", "ru"]},
        },
        "chroma": {
            "mode": "cpu_local",
            "persist_directory": str(tmp_path / "chroma"),
            "collection_name": collection,
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


@pytest.fixture
def test_app(tmp_path: Path, shared_tree: Path):
    config_path = write_test_config(tmp_path, shared_tree, f"col_{tmp_path.name}")
    app = create_app(config_path, tmp_path)
    return app, tmp_path, shared_tree, config_path
