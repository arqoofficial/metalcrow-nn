#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

if [[ "${1:-}" == "gpu" ]]; then
  echo "Starting parser services with GPU reservations..."
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d \
    redis main raw2docling_raw docling_raw2docling_clean00
  echo "Parser services started (GPU mode)."
  exit 0
fi

echo "Starting parser services..."
docker compose up -d redis main raw2docling_raw docling_raw2docling_clean00
echo "Parser services started."
