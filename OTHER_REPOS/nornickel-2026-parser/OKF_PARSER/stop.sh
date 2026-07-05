#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

if [[ "${1:-}" == "down" ]]; then
  echo "Stopping parser stack and removing containers..."
  docker compose down
  echo "Parser stack removed."
  exit 0
fi

echo "Stopping parser services..."
docker compose stop main raw2docling_raw docling_raw2docling_clean00 redis
echo "Parser services stopped."
