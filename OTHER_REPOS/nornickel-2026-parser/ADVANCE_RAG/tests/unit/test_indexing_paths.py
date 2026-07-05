"""Indexing path traversal and boundary tests."""

from pathlib import Path

import yaml

from app.config.settings import load_runtime_config
from app.data.chroma_adapter import create_chroma_adapter
from app.indexing.service import IndexingService


def _make_service(tmp_path: Path) -> IndexingService:
    shared = tmp_path / "SHARED"
    (shared / "01_docling_clean00" / "reports").mkdir(parents=True)
    (shared / "01_docling_clean00" / "reports" / "a.okf.md").write_text("ok", encoding="utf-8")
    data = {
        "api": {"version": "v1", "host": "0.0.0.0", "port": 8114},
        "shared": {"root": str(shared)},
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
            "collection_name": f"idx_paths_{tmp_path.name}",
        },
    }
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.dump(data), encoding="utf-8")
    runtime = load_runtime_config(cfg, tmp_path)
    adapter = create_chroma_adapter(runtime, tmp_path)
    return IndexingService(runtime, adapter, tmp_path)


def test_list_okf_files_rejects_traversal(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    files = service.list_okf_files("../../etc", "01_docling_clean00")
    assert files == []


def test_list_okf_files_returns_markdown_files(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    doc = (
        tmp_path
        / "SHARED"
        / "01_docling_clean00"
        / "reports"
        / "sample.pdf.md"
    )
    doc.write_text(
        "---\ntype: report\ntitle: Sample\n---\n\nBody",
        encoding="utf-8",
    )
    files = service.list_okf_files("reports", "01_docling_clean00")
    assert len(files) == 2
    assert any(item.name == "sample.pdf.md" for item in files)


def test_list_okf_files_returns_allowed_subtree_only(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    files = service.list_okf_files("reports", "01_docling_clean00")
    assert len(files) == 1
    assert files[0].name == "a.okf.md"