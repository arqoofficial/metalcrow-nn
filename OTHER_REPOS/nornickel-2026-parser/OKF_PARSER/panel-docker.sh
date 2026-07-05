#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed or not in PATH" >&2
  exit 1
fi

if docker compose ps --status running --services | rg -x "main" >/dev/null 2>&1; then
  exec docker compose exec main ./panel.sh "$@"
fi

echo "main service is not running, starting one-off panel container..." >&2
exec docker compose run --rm main ./panel.sh "$@"
