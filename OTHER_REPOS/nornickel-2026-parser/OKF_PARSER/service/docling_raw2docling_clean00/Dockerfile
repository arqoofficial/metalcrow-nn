FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
ENV CONTAINER_MODE=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:${PATH}"

USER parser
CMD ["python", "service/docling_raw2docling_clean00/worker.py"]
