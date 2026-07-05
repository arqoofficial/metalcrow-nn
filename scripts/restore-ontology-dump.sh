#!/usr/bin/env bash
# Restore the committed ontology snapshot into the running stack's `ontology` DB.
#
# The ontology base is NOT reconstructed identically by batch autoload: autoload
# only fires on an EMPTY DB, and it rebuilds embeddings in the background. This
# script instead loads the exact captured state (facts + prebuilt passage
# embeddings) from deploy/ontology_snapshot.dump, so a server matches local 1:1
# regardless of what its ontology volume already holds.
#
# The dump is stored via Git LFS — on a fresh clone run `git lfs pull` first.
#
#   Server:  cd /srv/metalcrow && git pull && git lfs pull && ./scripts/restore-ontology-dump.sh --prod
#   Local:   ./scripts/restore-ontology-dump.sh
#
# WARNING: drops and recreates the `ontology` database. Facts in it are replaced
# by the snapshot. The backend/app database is untouched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DUMP="${ROOT}/deploy/ontology_snapshot.dump"

COMPOSE=(docker compose)
if [[ "${1:-}" == "--prod" ]]; then
  COMPOSE=(docker compose -f compose.yml -f compose.prod.yml)
fi

if [[ ! -f "${DUMP}" ]]; then
  echo "error: ${DUMP} not found" >&2
  exit 1
fi
# Reject an unresolved LFS pointer (a few hundred bytes of text, not a dump).
if head -c 100 "${DUMP}" | grep -q "git-lfs.github.com/spec"; then
  echo "error: ${DUMP} is an unresolved Git LFS pointer — run 'git lfs pull' first" >&2
  exit 1
fi

cd "${ROOT}"
echo "→ ensuring db is up"
"${COMPOSE[@]}" up -d db
for _ in $(seq 1 30); do
  "${COMPOSE[@]}" exec -T db pg_isready -U "${POSTGRES_USER:-postgres}" >/dev/null 2>&1 && break
  sleep 2
done

echo "→ copying snapshot into db container"
"${COMPOSE[@]}" cp "${DUMP}" db:/tmp/ontology_snapshot.dump

echo "→ recreating and restoring the ontology database"
"${COMPOSE[@]}" exec -T db sh -c '
  export PGPASSWORD="${POSTGRES_PASSWORD}"
  U="${POSTGRES_USER:-postgres}"
  dropdb -U "$U" --if-exists ontology
  createdb -U "$U" ontology
  pg_restore -U "$U" -d ontology --no-owner --no-acl /tmp/ontology_snapshot.dump
  rm -f /tmp/ontology_snapshot.dump
'

echo "→ restarting ontology-knowledge-graph"
"${COMPOSE[@]}" restart ontology-knowledge-graph || true

echo "✓ restored. current ontology contents:"
"${COMPOSE[@]}" exec -T db sh -c '
  export PGPASSWORD="${POSTGRES_PASSWORD}"
  psql -U "${POSTGRES_USER:-postgres}" -d ontology -tAc "SELECT '\''docs=='\''||(SELECT count(*) FROM experiments.documents)||'\'' materials=='\''||(SELECT count(*) FROM experiments.materials)||'\'' embedded_passages=='\''||(SELECT count(*) FROM experiments.passage_index WHERE embedding IS NOT NULL)"
'
