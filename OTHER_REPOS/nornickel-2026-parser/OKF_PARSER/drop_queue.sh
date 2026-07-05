#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV="${UV:-uv}"
ENV_PATH="${ENV_PATH:-${ROOT_DIR}/.env}"
SHARED_ROOT="${SHARED_ROOT:-SHARED}"
RESTART_AFTER=false
SKIP_STOP=false
CONFIRMED=false

if [[ -n "${CONFIG_PATH:-}" ]]; then
  :
elif [[ -f "${ROOT_DIR}/config.yaml" ]]; then
  CONFIG_PATH="${ROOT_DIR}/config.yaml"
elif [[ -f "${ROOT_DIR}/config/local.yaml" ]]; then
  CONFIG_PATH="${ROOT_DIR}/config/local.yaml"
else
  CONFIG_PATH="${ROOT_DIR}/config.yaml"
fi

usage() {
  cat <<'EOF'
Usage: ./drop_queue.sh --yes [options]

Stop workers, delete Redis pipeline queues, and remove lock files under SHARED.

Options:
  --yes       Required. Confirm destructive cleanup.
  --no-stop   Do not stop workers/main before dropping queues (risky).
  --restart   Start parser services after cleanup (./start.sh).
  -h, --help  Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes)
      CONFIRMED=true
      ;;
    --no-stop)
      SKIP_STOP=true
      ;;
    --restart)
      RESTART_AFTER=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

if [[ "${CONFIRMED}" != "true" ]]; then
  echo "This will stop workers, delete Redis queues, and remove lock files." >&2
  echo "Re-run with: $0 --yes" >&2
  exit 1
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Config not found: ${CONFIG_PATH}" >&2
  exit 1
fi

cd "${ROOT_DIR}"

if [[ -f "${ENV_PATH}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_PATH}"
  set +a
fi

read -r STAGE0_KEY STAGE1_KEY < <(
  "${UV}" run python - <<'PY' "${CONFIG_PATH}" "${ENV_PATH}"
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from app.config.loader import load_config

config = load_config(sys.argv[1], sys.argv[2])
print(config.queues.raw2docling_raw, config.queues.docling_raw2docling_clean00)
PY
)

if [[ "${SKIP_STOP}" != "true" ]]; then
  echo "Stopping workers and main..."
  docker compose stop raw2docling_raw docling_raw2docling_clean00 main
fi

echo "Ensuring Redis is available..."
docker compose up -d redis

drop_queue_key() {
  local key="$1"
  local depth deleted
  depth="$(docker compose exec -T redis redis-cli LLEN "${key}")"
  deleted="$(docker compose exec -T redis redis-cli DEL "${key}")"
  echo "${key}: depth=${depth}, deleted=${deleted}"
}

echo "Dropping Redis queues..."
drop_queue_key "${STAGE0_KEY}"
drop_queue_key "${STAGE1_KEY}"

echo "Removing lock files under ${SHARED_ROOT}..."
SHARED_ROOT="${SHARED_ROOT}" "${ROOT_DIR}/clean_lock.sh"

echo "Queue drop complete."

if [[ "${RESTART_AFTER}" == "true" ]]; then
  echo "Restarting parser services..."
  "${ROOT_DIR}/start.sh"
fi
