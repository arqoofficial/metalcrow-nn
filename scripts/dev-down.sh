#!/usr/bin/env bash
# Stop metalcrow and (optionally) the parser stack.
#
# Usage:
#   ./scripts/dev-down.sh           # stop both (local CPU parser)
#   ./scripts/dev-down.sh --gpu
#   ./scripts/dev-down.sh --prod
#   ./scripts/dev-down.sh --no-parser
#   ./scripts/dev-down.sh -v        # also remove volumes (metalcrow)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=stack-common.sh
source "${SCRIPT_DIR}/stack-common.sh"

STACK_MODE=local
WITH_PARSER=true
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu) STACK_MODE=gpu; shift ;;
    --prod) STACK_MODE=prod; shift ;;
    --no-parser) WITH_PARSER=false; shift ;;
    -h|--help)
      cat <<'EOF'
Usage: dev-down.sh [OPTIONS] [docker compose down args…]

  --gpu          match parser stack started with --gpu
  --prod         match metalcrow prod overlay
  --no-parser    stop metalcrow only
  -v             remove volumes (pass-through to docker compose down)
EOF
      exit 0
      ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac
done

cd "${STACK_ROOT}"

echo "→ stopping metalcrow"
if ((${#EXTRA_ARGS[@]} > 0)); then
  stack_metalcrow_compose down "${EXTRA_ARGS[@]}"
else
  stack_metalcrow_compose down
fi

if [[ "${WITH_PARSER}" == true && -f "${PARSER_DIR}/docker-compose.yml" ]]; then
  stack_parser_compose_args
  stack_parser_profile_args
  echo "→ stopping parser"
  (
    cd "${PARSER_DIR}"
    if ((${#EXTRA_ARGS[@]} > 0)); then
      docker compose "${PARSER_PROFILE_ARGS[@]}" "${PARSER_COMPOSE_ARGS[@]}" down "${EXTRA_ARGS[@]}"
    else
      docker compose "${PARSER_PROFILE_ARGS[@]}" "${PARSER_COMPOSE_ARGS[@]}" down
    fi
  )
fi

echo "✓ stack stopped"
