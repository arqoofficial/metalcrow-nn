#!/usr/bin/env bash
# Download SHARED/ from a public Yandex Disk folder and unpack into the parser tree.
#
# Usage:
#   ./scripts/fetch-shared-yandex.sh
#   ./scripts/fetch-shared-yandex.sh --url 'https://disk.yandex.ru/d/PBlYaUPyGGIw_w'
#   ./scripts/fetch-shared-yandex.sh --keep-zip
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PARSER_DIR="${STACK_ROOT}/services/nornickel-2026-parser"
TARGET_PARENT="${PARSER_DIR}"
PUBLIC_URL="https://disk.yandex.ru/d/PBlYaUPyGGIw_w"
KEEP_ZIP=false
TMP_DIR=""

usage() {
  cat <<'EOF'
Usage: fetch-shared-yandex.sh [OPTIONS]

  Downloads SHARED/ from a public Yandex Disk link and extracts it to
  services/nornickel-2026-parser/SHARED/ (parser bind mount).

Options:
  --url URL     Public Yandex Disk folder URL (default: hackathon SHARED link)
  --keep-zip    Do not delete the downloaded zip after extraction
  -h, --help    Show this help
EOF
  exit "${1:-0}"
}

cleanup() {
  if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
    rm -rf "${TMP_DIR}"
  fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)
      PUBLIC_URL="${2:?missing value for --url}"
      shift 2
      ;;
    --keep-zip) KEEP_ZIP=true; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 1 ;;
  esac
done

for cmd in curl python3 unzip; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "required command not found: ${cmd}" >&2
    exit 1
  fi
done

TMP_DIR="$(mktemp -d)"
ZIP_PATH="${TMP_DIR}/SHARED.zip"

echo "→ resolving download URL for SHARED/ …"
DOWNLOAD_URL="$(
  curl -fsS \
    --get \
    --data-urlencode "public_key=${PUBLIC_URL}" \
    --data-urlencode "path=/SHARED" \
    "https://cloud-api.yandex.net/v1/disk/public/resources/download" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["href"])'
)"

echo "→ downloading SHARED.zip (may take several minutes) …"
curl -fL --progress-bar -o "${ZIP_PATH}" "${DOWNLOAD_URL}"

echo "→ extracting into ${TARGET_PARENT} …"
mkdir -p "${TARGET_PARENT}"
unzip -q -o "${ZIP_PATH}" -d "${TARGET_PARENT}"

if [[ "${KEEP_ZIP}" == true ]]; then
  cp "${ZIP_PATH}" "${TARGET_PARENT}/SHARED.zip"
  echo "  kept archive: ${TARGET_PARENT}/SHARED.zip"
fi

echo "✓ SHARED ready at ${PARSER_DIR}/SHARED"
echo "  next: make up-prod   (or make up locally)"
