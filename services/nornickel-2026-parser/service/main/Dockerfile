FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN useradd --create-home --uid 1000 parser

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENV CONTAINER_MODE=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:${PATH}"

USER parser
CMD ["uvicorn", "service.main.main:app", "--host", "0.0.0.0", "--port", "8114"]
