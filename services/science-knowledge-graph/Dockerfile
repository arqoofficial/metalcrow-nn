FROM python:3.11

ENV PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app/

ENV PATH="/app/.venv/bin:$PATH"

# Independent uv project (own uv.lock) — spaCy/scispaCy stay out of backend stack.
COPY pyproject.toml uv.lock ./
COPY models/en_core_sci_sm-0.5.4.tar.gz ./models/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . /app/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uvicorn", "science_kg.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
