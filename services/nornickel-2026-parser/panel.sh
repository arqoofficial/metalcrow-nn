#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV="${UV:-uv}"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env}"
REFRESH_SEC="${REFRESH_SEC:-3}"

if [[ -n "${CONFIG_PATH:-}" ]]; then
  :
elif [[ -f "${ROOT_DIR}/config.yaml" ]]; then
  CONFIG_PATH="${ROOT_DIR}/config.yaml"
elif [[ -f "${ROOT_DIR}/config/local.yaml" ]]; then
  CONFIG_PATH="${ROOT_DIR}/config/local.yaml"
else
  CONFIG_PATH="${ROOT_DIR}/config.yaml"
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Config not found: ${CONFIG_PATH}" >&2
  echo "Copy config.yaml.example to config.yaml, or use the bundled config/local.yaml for Docker + host panel." >&2
  echo "Set shared_root to ${ROOT_DIR}/SHARED and admin_panel.api_base_url to http://127.0.0.1:8114" >&2
  exit 1
fi

cd "${ROOT_DIR}"

PANEL_ARGS=(--config "${CONFIG_PATH}" --env-file "${ENV_FILE}")

if [[ $# -eq 0 ]]; then
  exec "${UV}" run -m admin_panel run \
    "${PANEL_ARGS[@]}" \
    --refresh-sec "${REFRESH_SEC}"
fi

exec "${UV}" run -m admin_panel "$1" "${PANEL_ARGS[@]}" "${@:2}"
