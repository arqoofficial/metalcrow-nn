"""Dense indexing and retrieval speed smoke test."""

from __future__ import annotations

import os
import time
from pathlib import Path

import chromadb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ONNX_DIR = PROJECT_ROOT / "assets" / "models" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2"


def test_dense_index_and_retrieve_under_half_second(tmp_path: Path) -> None:
    model_file = ONNX_DIR / "onnx" / "model.onnx"
    assert model_file.exists(), "Run ./load_model.sh before running speed tests"
    os.environ["ADVANCE_RAG_ONNX_MODEL_DIR"] = str(ONNX_DIR)

    collection = chromadb.PersistentClient(path=str(tmp_path / "chroma")).get_or_create_collection(
        name="dense_speed_smoke",
        metadata={"hnsw:space": "cosine"},
    )

    started = time.perf_counter()
    collection.upsert(
        ids=["dense-speed-doc"],
        documents=["Nickel production forecast for dense retrieval test."],
        metadatas=[{"source_subfolder": "01_docling_clean00", "path": "dense/smoke.md"}],
    )
    index_elapsed = time.perf_counter() - started

    started = time.perf_counter()
    result = collection.query(query_texts=["nickel forecast"], n_results=1)
    query_elapsed = time.perf_counter() - started

    ids = (result.get("ids") or [[]])[0]
    assert ids and ids[0] == "dense-speed-doc"
    assert index_elapsed < 0.5, f"Indexing took {index_elapsed:.3f}s, expected < 0.5s"
    assert query_elapsed < 0.5, f"Query took {query_elapsed:.3f}s, expected < 0.5s"
