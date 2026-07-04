#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

if [[ "${1:-}" == "preload-models" ]]; then
  echo "Preloading Docling/OCR models into ./SHARED/MODELS ..."
  mkdir -p "${ROOT_DIR}/SHARED/MODELS"
  docker compose run --rm -e PYTHONUNBUFFERED=1 raw2docling_raw \
    python -u scripts/preload_models.py
  echo "Model preload complete."
  exit 0
fi

echo "Restarting parser services..."
docker compose restart main raw2docling_raw docling_raw2docling_clean00
echo "Restart complete."
