#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV="${UV:-uv}"
ENV_PATH="${ENV_PATH:-${ROOT_DIR}/.env}"

if [[ -n "${CONFIG_PATH:-}" ]]; then
  :
elif [[ -f "${ROOT_DIR}/config.yaml" ]]; then
  CONFIG_PATH="${ROOT_DIR}/config.yaml"
elif [[ -f "${ROOT_DIR}/config/local.yaml" ]]; then
  CONFIG_PATH="${ROOT_DIR}/config/local.yaml"
else
  CONFIG_PATH="${ROOT_DIR}/config.yaml"
fi

cd "${ROOT_DIR}"

if [[ -f "${ENV_PATH}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_PATH}"
  set +a
fi

API_BASE_URL="${API_BASE_URL:-}"
if [[ -z "${API_BASE_URL}" && -f "${CONFIG_PATH}" ]]; then
  API_BASE_URL="$(
    "${UV}" run python - <<'PY' "${CONFIG_PATH}" "${ENV_PATH}"
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from app.config.loader import load_config

config = load_config(sys.argv[1], sys.argv[2])
print(config.resolved_admin_api_base_url)
PY
  )"
fi

if [[ -z "${API_BASE_URL}" ]]; then
  echo "API base URL is not configured" >&2
  exit 1
fi

response_file="$(mktemp)"
http_code="$(
  curl -sS -o "${response_file}" -w "%{http_code}" \
    -X POST "${API_BASE_URL%/}/api/v1/reindex" \
    -H "Content-Type: application/json" \
    -d "{}" || echo "000"
)"

if [[ "${http_code}" != "202" ]]; then
  echo "Reindex failed with HTTP ${http_code}" >&2
  cat "${response_file}" >&2 || true
  rm -f "${response_file}"
  exit 1
fi

enqueued="$("${UV}" run python - <<'PY' "${response_file}"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("enqueued", 0))
PY
)"
echo "Reindex accepted: enqueued=${enqueued}"
rm -f "${response_file}"
