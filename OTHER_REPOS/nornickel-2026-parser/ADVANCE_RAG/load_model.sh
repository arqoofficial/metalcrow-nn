#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-download}"

if [[ "${MODE}" != "download" && "${MODE}" != "--no-download" ]]; then
  echo "Usage: $0 [download|--no-download]" >&2
  exit 1
fi

NO_DOWNLOAD=0
if [[ "${MODE}" == "--no-download" ]]; then
  NO_DOWNLOAD=1
fi

PYTHON_BIN="${PYTHON_BIN:-${SCRIPT_DIR}/.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

ONNX_DIR="${SCRIPT_DIR}/assets/models/chroma/onnx_models/all-MiniLM-L6-v2"
NLTK_DIR="${SCRIPT_DIR}/assets/nltk_data"
RERANKER_DIR="${SCRIPT_DIR}/assets/reranker"
STOPWORDS_FILE="${RERANKER_DIR}/stopwords.txt"

mkdir -p "${ONNX_DIR}" "${NLTK_DIR}" "${RERANKER_DIR}"

export ADVANCE_RAG_ONNX_MODEL_DIR="${ONNX_DIR}"
export ADVANCE_RAG_NLTK_DATA="${NLTK_DIR}"
export NLTK_DATA="${NLTK_DIR}"
export ADVANCE_RAG_RERANKER_STOPWORDS="${STOPWORDS_FILE}"
export ADVANCE_RAG_NO_DOWNLOAD="${NO_DOWNLOAD}"

"${PYTHON_BIN}" - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

NO_DOWNLOAD = os.environ.get("ADVANCE_RAG_NO_DOWNLOAD", "0") == "1"
onnx_dir = Path(os.environ["ADVANCE_RAG_ONNX_MODEL_DIR"]).resolve()
nltk_dir = Path(os.environ["ADVANCE_RAG_NLTK_DATA"]).resolve()
stopwords_file = Path(os.environ["ADVANCE_RAG_RERANKER_STOPWORDS"]).resolve()

required_onnx = [
    "onnx/config.json",
    "onnx/model.onnx",
    "onnx/special_tokens_map.json",
    "onnx/tokenizer_config.json",
    "onnx/tokenizer.json",
    "onnx/vocab.txt",
]
required_nltk_any = [
    ("tokenizers/punkt", "tokenizers/punkt.zip"),
    ("tokenizers/punkt_tab", "tokenizers/punkt_tab.zip"),
    ("corpora/wordnet", "corpora/wordnet.zip"),
    ("corpora/omw-1.4", "corpora/omw-1.4.zip"),
    ("corpora/stopwords", "corpora/stopwords.zip"),
]


def check_assets() -> list[str]:
    missing: list[str] = []
    for rel in required_onnx:
        if not (onnx_dir / rel).exists():
            missing.append(f"ONNX missing: {onnx_dir / rel}")
    for primary, fallback in required_nltk_any:
        if not (nltk_dir / primary).exists() and not (nltk_dir / fallback).exists():
            missing.append(f"NLTK missing: {nltk_dir / primary} or {nltk_dir / fallback}")
    if not stopwords_file.exists():
        missing.append(f"Stopwords file missing: {stopwords_file}")
    return missing


if not NO_DOWNLOAD:
    import nltk

    nltk.data.path.insert(0, str(nltk_dir))
    for resource in ("punkt", "punkt_tab", "wordnet", "omw-1.4", "stopwords"):
        nltk.download(resource, quiet=True, download_dir=str(nltk_dir))

    # Force Chroma ONNX model extraction into project assets.
    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = onnx_dir
    embedder = ONNXMiniLM_L6_V2()
    embedder(["model warmup"])

    from nltk.corpus import stopwords as nltk_stopwords

    stopword_set = sorted(
        set(nltk_stopwords.words("english")) | set(nltk_stopwords.words("russian"))
    )
    stopwords_file.write_text("\n".join(stopword_set) + "\n", encoding="utf-8")

missing_assets = check_assets()
if missing_assets:
    print("Asset verification failed:")
    for item in missing_assets:
        print(f" - {item}")
    raise SystemExit(1)

print("Assets ready:")
print(f" - ONNX model dir: {onnx_dir}")
print(f" - NLTK data dir : {nltk_dir}")
print(f" - Reranker words: {stopwords_file}")
PY

