#!/usr/bin/env bash
# Bring up metalcrow + nornickel parser with sensible defaults.
#
# Usage:
#   ./scripts/dev-up.sh              # local CPU (default)
#   ./scripts/dev-up.sh --gpu        # parser on CUDA
#   ./scripts/dev-up.sh --prod       # server overlay (frontend :80)
#   ./scripts/dev-up.sh --no-parser  # metalcrow only (L1 stub fallback)
#   ./scripts/dev-up.sh --skip-models
#   ./scripts/dev-up.sh --models-only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=stack-common.sh
source "${SCRIPT_DIR}/stack-common.sh"

STACK_MODE=local   # local | gpu | prod
WITH_PARSER=true
SKIP_MODELS=false
MODELS_ONLY=false

usage() {
  cat <<'EOF'
Usage: dev-up.sh [OPTIONS]

  (no flags)     local CPU parser + metalcrow (default)
  --gpu          parser on CUDA
  --prod         server overlay (frontend :80)
  --no-parser    metalcrow only (start parser separately for ingest)
  --skip-models  skip model preload check/download
  --models-only  preload models, do not start stacks
  -h, --help     show this help
EOF
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu) STACK_MODE=gpu; shift ;;
    --prod) STACK_MODE=prod; shift ;;
    --no-parser) WITH_PARSER=false; shift ;;
    --skip-models) SKIP_MODELS=true; shift ;;
    --models-only) MODELS_ONLY=true; WITH_PARSER=true; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 1 ;;
  esac
done

cd "${STACK_ROOT}"

stack_print_startup_notice

stack_ensure_network

if [[ "${WITH_PARSER}" == true ]]; then
  stack_ensure_parser_dir
  stack_ensure_env_files
  stack_ensure_parser_shared

  if [[ "${SKIP_MODELS}" == false ]]; then
    stack_preload_models_if_needed
  fi

  if [[ "${MODELS_ONLY}" == true ]]; then
    echo "✓ parser models ready"
    exit 0
  fi

  stack_parser_compose_args
  echo "→ starting parser stack (${STACK_MODE})"
  echo "  (сборка образов при первом запуске может занять много времени — не прерывайте)"
  (
    cd "${PARSER_DIR}"
    docker compose "${PARSER_COMPOSE_ARGS[@]}" up -d --build
  )
else
  stack_ensure_env_files
fi

echo "→ starting metalcrow (${STACK_MODE})"
echo "  (сборка образов при первом запуске может занять много времени — не прерывайте)"
stack_metalcrow_compose up -d --build

echo ""
echo "✓ stack is up"
if [[ "${WITH_PARSER}" == true && "${STACK_MODE}" != "prod" ]]; then
  echo "  parser health: curl http://localhost:8114/health"
fi
if [[ "${STACK_MODE}" == "prod" ]]; then
  echo "  frontend:      http://<server-ip>/"
else
  echo "  frontend:      http://localhost:5173"
  echo "  backend docs:  http://localhost:8000/docs"
fi
