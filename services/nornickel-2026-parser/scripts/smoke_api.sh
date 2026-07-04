#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8114}"

curl -fsS "${API_BASE_URL}/api/v1/statistics" >/dev/null
echo "statistics: ok"

curl -fsS -X POST "${API_BASE_URL}/api/v1/reindex" \
  -H "Content-Type: application/json" \
  -d '{}' >/dev/null
echo "reindex: ok"

curl -fsS "${API_BASE_URL}/api/v1/files/tree?root=UPLOAD_DATA" >/dev/null
echo "tree: ok"

echo "smoke_api: all checks passed"
