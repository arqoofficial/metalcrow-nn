# syntax=docker/dockerfile:1

FROM python:3.12-slim

# cpu (default): small image with torch+cpu wheels (~600 MB compressed).
# gpu: CUDA torch from PyPI via uv.lock.gpu (~9 GB compressed).
ARG TORCH_VARIANT=cpu

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/home/parser/.cache/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gosu \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 parser \
    && chown parser:parser /app

USER parser

COPY --chown=parser:parser pyproject.toml uv.lock uv.lock.gpu ./
RUN --mount=type=cache,target=/home/parser/.cache/uv,uid=1000,gid=1000 \
    if [ "${TORCH_VARIANT}" = "gpu" ]; then cp uv.lock.gpu uv.lock; fi \
    && uv sync --frozen --no-dev --no-install-project

COPY --chown=parser:parser . .
COPY --chown=parser:parser config/docker.yaml /app/config.yaml

RUN --mount=type=cache,target=/home/parser/.cache/uv,uid=1000,gid=1000 \
    uv sync --frozen --no-dev

USER root
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV CONTAINER_MODE=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:${PATH}"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "service.main.main:app", "--host", "0.0.0.0", "--port", "8114"]
