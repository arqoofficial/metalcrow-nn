#!/usr/bin/env bash
# Bulk-load precomputed facts + vectors from parser SHARED/ into Neo4j.
#
# Offline path (no spaCy re-run, no embedding API):
#   SHARED/facts/*.json  +  SHARED/vectors/  +  SHARED/**/*.md
#
# Server (hackathon deploy):
#   cd /srv/metalcrow
#   ./scripts/load-precomputed-facts.sh --prod
#
# Local:
#   ./scripts/load-precomputed-facts.sh
#
# Long run (~2–5 min for ~600 docs) — use tmux/screen on SSH.
#
# Usage:
#   load-precomputed-facts.sh [--prod] [--shared DIR] [--limit N] [--skip-existing]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=stack-common.sh
source "${SCRIPT_DIR}/stack-common.sh"

STACK_MODE=local
SHARED_DIR="${PARSER_SHARED}"
EXTRA_ARGS=()

usage() {
  cat <<'EOF'
Usage: load-precomputed-facts.sh [OPTIONS]

  Loads SHARED/facts + SHARED/vectors into Neo4j via science-knowledge-graph.
  Requires facts/ and vectors/ (entities.npy, entities.jsonl) under SHARED.

Options:
  --prod           use compose.prod.yml (server overlay, frontend :80)
  --shared DIR     path to SHARED (default: services/nornickel-2026-parser/SHARED)
  --limit N        load only first N fact files (smoke test)
  --skip-existing  skip documents already present in Neo4j
  -h, --help       show this help
EOF
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prod) STACK_MODE=prod; shift ;;
    --shared)
      SHARED_DIR="${2:?missing value for --shared}"
      shift 2
      ;;
    --limit)
      EXTRA_ARGS+=(--limit "${2:?missing value for --limit}")
      shift 2
      ;;
    --skip-existing) EXTRA_ARGS+=(--skip-existing); shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 1 ;;
  esac
done

cd "${STACK_ROOT}"

if [[ ! -d "${SHARED_DIR}/facts" ]]; then
  echo "error: facts dir not found: ${SHARED_DIR}/facts" >&2
  exit 1
fi
if [[ ! -f "${SHARED_DIR}/vectors/entities.npy" || ! -f "${SHARED_DIR}/vectors/entities.jsonl" ]]; then
  echo "error: vectors not found under ${SHARED_DIR}/vectors" >&2
  echo "  run embed_facts.py first, or fetch SHARED via ./scripts/fetch-shared-yandex.sh" >&2
  exit 1
fi

FACT_COUNT="$(find "${SHARED_DIR}/facts" -name '*.json' | wc -l | tr -d ' ')"
echo "→ SHARED: ${SHARED_DIR}"
echo "  fact files: ${FACT_COUNT}"
echo "→ ensuring neo4j + science-knowledge-graph are up (${STACK_MODE})"
stack_metalcrow_compose up -d neo4j science-knowledge-graph

echo "→ loading precomputed facts into Neo4j (PYTHONPATH=/app required for compose run)…"
stack_metalcrow_compose run --rm \
  -e PYTHONPATH=/app \
  -v "${SHARED_DIR}:/shared:ro" \
  science-knowledge-graph \
  python scripts/load_precomputed_facts.py \
    /shared/facts \
    /shared/vectors \
    --md-dir /shared \
    "${EXTRA_ARGS[@]}"

echo "✓ Neo4j load finished"
