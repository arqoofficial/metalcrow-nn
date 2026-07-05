#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

ensure_env_file() {
  if [[ -f ".env" ]]; then
    return 0
  fi
  if [[ -f ".env.example" ]]; then
    cp ".env.example" ".env"
    echo "Created .env from .env.example"
  else
    touch ".env"
    echo "Created empty .env"
  fi
}

wait_for_api() {
  local attempts=20
  local delay_sec=0.5
  local i
  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS "http://127.0.0.1:8115/ready" \
      | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("status") == "ready" else 1)' \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep "${delay_sec}"
  done
  return 1
}

CMD="${1:-status}"
shift || true

case "${CMD}" in
  start)
    ensure_env_file
    docker compose up -d
    wait_for_api || true
    ;;
  stop)
    docker compose down
    ;;
  rerun)
    ensure_env_file
    docker compose down
    docker compose up -d
    wait_for_api || true
    ;;
  status)
    ensure_env_file
    docker compose ps
    wait_for_api || true
    echo
    echo "Health:"
    curl -fsS "http://127.0.0.1:8115/health" || true
    echo
    echo "Readiness:"
    curl -fsS "http://127.0.0.1:8115/ready" || true
    echo
    echo "Runtime:"
    curl -fsS "http://127.0.0.1:8115/admin/runtime" | python3 -m json.tool 2>/dev/null || true
    echo
    ;;
  index-doc)
    # Usage: ./panel-docker.sh index-doc "<path>"
    JSON_PAYLOAD="$(python -c 'import json,sys; print(json.dumps({"path": sys.argv[1]}))' "${1:-}")"
    curl -sS -X POST "http://127.0.0.1:8115/api/v1/index_doc" \
      -H "Content-Type: application/json" \
      -d "${JSON_PAYLOAD}"
    ;;
  index-path)
    # Usage: ./panel-docker.sh index-path "<path>"
    JSON_PAYLOAD="$(python -c 'import json,sys; print(json.dumps({"path": sys.argv[1]}))' "${1:-}")"
    curl -sS -X POST "http://127.0.0.1:8115/api/v1/index_path" \
      -H "Content-Type: application/json" \
      -d "${JSON_PAYLOAD}"
    ;;
  *)
    echo "Unknown command: ${CMD}" >&2
    echo "Usage: $0 {start|stop|rerun|status|index-doc|index-path}" >&2
    exit 1
    ;;
esac
