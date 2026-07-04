FROM python:3.11

ENV PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app/

ENV PATH="/app/.venv/bin:$PATH"

# Build from repo root: docker compose build svc-parse-docling
COPY pyproject.toml uv.lock ./
COPY packages/tool_sdk ./packages/tool_sdk
COPY services/parse-docling/pyproject.toml ./services/parse-docling/

WORKDIR /app/services/parse-docling

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --package svc-parse-docling --no-dev

COPY services/parse-docling ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package svc-parse-docling --no-dev

CMD ["celery", "-A", "parse_docling.celery_app", "worker", "--loglevel=info", "-Q", "parse.docling", "--concurrency=1"]
