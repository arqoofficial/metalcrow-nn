# shellcheck shell=bash
# Shared paths and helpers for dev stack scripts (sourced, not executed).

stack_root_dir() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

STACK_ROOT="$(stack_root_dir)"
PARSER_DIR="${STACK_ROOT}/services/nornickel-2026-parser"
PARSER_OVERRIDE="${STACK_ROOT}/services/nornickel-parser.override.yml"

# Dockerfiles use BuildKit cache mounts (uv wheel cache). Required on Docker < 23.
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

stack_ensure_network() {
  if ! docker network inspect metalcrow-net >/dev/null 2>&1; then
    echo "→ creating docker network metalcrow-net"
    docker network create metalcrow-net
  fi
}

stack_ensure_parser_dir() {
  if [[ ! -f "${PARSER_DIR}/docker-compose.yml" ]]; then
    echo "error: parser not found at ${PARSER_DIR}" >&2
    exit 1
  fi
}

stack_ensure_env_files() {
  if [[ ! -f "${STACK_ROOT}/.env" ]]; then
    echo "→ copying .env.example → .env"
    cp "${STACK_ROOT}/.env.example" "${STACK_ROOT}/.env"
  fi
  if [[ ! -f "${PARSER_DIR}/.env" ]]; then
    echo "→ copying parser .env.example → .env"
    cp "${PARSER_DIR}/.env.example" "${PARSER_DIR}/.env"
  fi
}

# Parser SHARED/ is the single file store (source of truth for raw, OKF, chunks).
PARSER_SHARED="${PARSER_DIR}/SHARED"

stack_ensure_parser_shared() {
  mkdir -p "${PARSER_SHARED}/MODELS"
  touch "${PARSER_SHARED}/.gitkeep" "${PARSER_SHARED}/MODELS/.gitkeep"
}

# Sets PARSER_COMPOSE_ARGS array for the current mode (cpu or gpu).
stack_parser_compose_args() {
  PARSER_COMPOSE_ARGS=(
    -f "${PARSER_DIR}/docker-compose.yml"
    -f "${PARSER_OVERRIDE}"
  )
  if [[ "${STACK_MODE}" == "gpu" ]]; then
    PARSER_COMPOSE_ARGS+=(-f "${PARSER_DIR}/docker-compose.gpu.yml")
  fi
}

# Run docker compose for metalcrow (local uses default compose.override.yml merge).
stack_metalcrow_compose() {
  if [[ "${STACK_MODE}" == "prod" ]]; then
    docker compose -f compose.yml -f compose.prod.yml "$@"
  else
    docker compose "$@"
  fi
}

# Warn before first full stack bring-up (build + optional model download).
stack_print_startup_notice() {
  [[ "${MODELS_ONLY:-false}" == true ]] && return 0

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Первая загрузка (make up) может быть ОЧЕНЬ долгой — подождите."
  echo ""
  echo "  · Docker собирает образы (первый запуск занимает больше всего времени)"
  if [[ "${WITH_PARSER:-true}" == true && "${SKIP_MODELS:-false}" == false ]]; then
    echo "  · Скачиваются модели Docling/OCR (~10–20 мин, прогресс ниже)"
  fi
  echo ""
  echo "  Не прерывайте (Ctrl+C) — дождитесь сообщения «✓ stack is up»."
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
}

stack_preload_models_if_needed() {
  stack_ensure_parser_shared
  stack_parser_compose_args

  echo "→ checking parser model cache"
  (
    cd "${PARSER_DIR}"
    docker compose "${PARSER_COMPOSE_ARGS[@]}" build raw2docling_raw
    if docker compose "${PARSER_COMPOSE_ARGS[@]}" run --rm raw2docling_raw \
      python scripts/preload_models.py --check-only >/dev/null 2>&1; then
      echo "  models already cached in services/nornickel-2026-parser/SHARED/MODELS"
      return 0
    fi
    echo "→ preloading Docling/OCR models (первый запуск ~10–20 мин, прогресс ниже — подождите)…"
    docker compose "${PARSER_COMPOSE_ARGS[@]}" run --rm \
      -e PYTHONUNBUFFERED=1 \
      raw2docling_raw \
      python -u scripts/preload_models.py
  )
}
