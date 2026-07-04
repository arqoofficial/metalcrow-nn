#!/usr/bin/env bash
# Rebuild Docker images without starting stacks.
#
# Usage:
#   ./scripts/dev-build.sh                 # parser (CPU) + metalcrow
#   ./scripts/dev-build.sh --gpu           # parser on CUDA + metalcrow
#   ./scripts/dev-build.sh --prod          # metalcrow prod overlay (+ parser CPU)
#   ./scripts/dev-build.sh --no-parser     # metalcrow only
#   ./scripts/dev-build.sh --parser-only   # parser only (CPU)
#   ./scripts/dev-build.sh --parser-only --gpu
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=stack-common.sh
source "${SCRIPT_DIR}/stack-common.sh"

STACK_MODE=local   # local | gpu | prod
WITH_PARSER=true
PARSER_ONLY=false

usage() {
  cat <<'EOF'
Usage: dev-build.sh [OPTIONS]

  (no flags)     build parser (CPU) + metalcrow images
  --gpu          build parser with CUDA + metalcrow
  --prod         build metalcrow prod overlay (+ parser CPU)
  --no-parser    build metalcrow images only
  --parser-only  build parser images only
  -h, --help     show this help
EOF
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu) STACK_MODE=gpu; shift ;;
    --prod) STACK_MODE=prod; shift ;;
    --no-parser) WITH_PARSER=false; shift ;;
    --parser-only) PARSER_ONLY=true; WITH_PARSER=true; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 1 ;;
  esac
done

cd "${STACK_ROOT}"

stack_ensure_env_files

if [[ "${WITH_PARSER}" == true ]]; then
  stack_ensure_parser_dir
  stack_parser_compose_args
  echo "→ building parser images (${STACK_MODE})"
  (
    cd "${PARSER_DIR}"
    docker compose "${PARSER_COMPOSE_ARGS[@]}" build
  )
fi

if [[ "${PARSER_ONLY}" == true ]]; then
  echo "✓ parser images built"
  exit 0
fi

echo "→ building metalcrow images (${STACK_MODE})"
stack_metalcrow_compose build

echo "✓ images built"
