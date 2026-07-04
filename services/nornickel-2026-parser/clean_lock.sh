#!/usr/bin/env bash
set -euo pipefail

SHARED_ROOT="${SHARED_ROOT:-SHARED}"

if [[ ! -d "${SHARED_ROOT}" ]]; then
  exit 0
fi

find "${SHARED_ROOT}" -type f \( -name "*.upload.lock" -o -name "*.worker.lock" \) -print -delete 2>/dev/null || true
