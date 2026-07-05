#!/usr/bin/env bash
# Load precomputed ontology batches into the ontology DB — mirror of
# load-precomputed-facts.sh for science-knowledge-graph.
#
# The batches (ontology/batches/okf-*.json) are baked into the
# ontology-knowledge-graph image and autoloaded by service_init on an EMPTY DB.
# This script forces an explicit, idempotent (re)load into a RUNNING stack —
# e.g. after new batches were committed and the image rebuilt, when the DB is
# no longer empty and autoload is skipped.
#
# Server:  cd /srv/metalcrow && ./scripts/load-ontology-batches.sh --prod
# Local:   ./scripts/load-ontology-batches.sh
#
# Usage: load-ontology-batches.sh [--prod] [--glob PATTERN]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=stack-common.sh
source "${SCRIPT_DIR}/stack-common.sh"

STACK_MODE=local
GLOB="okf-*.json"

usage() {
  cat <<'EOF'
Usage: load-ontology-batches.sh [OPTIONS]

  Loads ontology/batches into the ontology DB via the ontology-knowledge-graph
  container (idempotent, ON CONFLICT DO NOTHING). Batches ship inside the image.

Options:
  --prod          use compose.prod.yml (server overlay)
  --glob PATTERN  batch filename mask (default: okf-*.json)
  -h, --help      show this help
EOF
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prod) STACK_MODE=prod; shift ;;
    --glob) GLOB="${2:?missing value for --glob}"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 1 ;;
  esac
done

cd "${STACK_ROOT}"

echo "→ ensuring db + ontology-knowledge-graph are up (${STACK_MODE})"
stack_metalcrow_compose up -d db ontology-knowledge-graph

echo "→ loading ontology/batches (${GLOB}) into the ontology DB…"
stack_metalcrow_compose run --rm \
  ontology-knowledge-graph \
  python -m ontology.loader --dir ontology/batches --glob "${GLOB}"

echo "✓ ontology batches loaded"
