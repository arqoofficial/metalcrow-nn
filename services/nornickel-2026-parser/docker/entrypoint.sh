#!/usr/bin/env bash
set -euo pipefail

SHARED_ROOT="${SHARED_ROOT:-/mnt/nfs/SHARED}"
PARSER_USER="${PARSER_USER:-parser}"

mkdir -p \
  "${SHARED_ROOT}/UPLOAD_DATA" \
  "${SHARED_ROOT}/RAW_DATA" \
  "${SHARED_ROOT}/00_docling_raw" \
  "${SHARED_ROOT}/01_docling_clean00"

if [ "$(id -u)" -eq 0 ]; then
  chown -R "${PARSER_USER}:${PARSER_USER}" "${SHARED_ROOT}"
  exec gosu "${PARSER_USER}" "$@"
fi

exec "$@"
