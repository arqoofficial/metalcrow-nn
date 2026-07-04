FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# `git` (installed above) is REQUIRED: `invisible_playwright` is a git+https dep.
COPY services/article-fetcher/pyproject.toml services/article-fetcher/uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# --- Headless stealth-browser fetch tier (flag-gated by HEADLESS_FETCH_ENABLED) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgtk-3-0 libdbus-glib-1-2 libasound2 libx11-xcb1 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libxtst6 libpci3 libxcursor1 \
    libgdk-pixbuf-2.0-0 libpangocairo-1.0-0 libcairo-gobject2 fonts-liberation \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY services/article-fetcher/app ./app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

RUN python -m invisible_playwright fetch

EXPOSE 8200

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200"]
