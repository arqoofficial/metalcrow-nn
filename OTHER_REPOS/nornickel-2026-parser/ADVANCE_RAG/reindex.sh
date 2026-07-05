#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

usage() {
  echo "Usage:"
  echo "  $0 doc <path>"
  echo "  $0 path <path>"
}

MODE="${1:-}"
if [[ -z "${MODE}" ]]; then
  usage
  exit 1
fi
shift || true

case "${MODE}" in
  doc)
    if [[ "${#}" -lt 1 ]]; then
      usage
      exit 1
    fi
    exec ./panel-docker.sh index-doc "$1"
    ;;
  path)
    if [[ "${#}" -lt 1 ]]; then
      usage
      exit 1
    fi
    exec ./panel-docker.sh index-path "$1"
    ;;
  *)
    usage
    exit 1
    ;;
esac
