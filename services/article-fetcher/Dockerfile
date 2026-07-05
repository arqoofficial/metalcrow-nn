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
# The `invisible_playwright` git dep is installed by the `uv pip install` above
# (declared in pyproject.toml). The block below bakes its patched Firefox 150
# binary + the system libraries Firefox needs into the image.
#
# COST: this adds ~300-400 MB to the image (~100 MB patched Firefox binary +
# ~150-250 MB of GTK/X/font libs + xvfb). That is WHY the block itself is
# build-arg-gated (INSTALL_HEADLESS, default false) as well as flag-gated at
# runtime (HEADLESS_FETCH_ENABLED) — a default build skips this layer entirely
# and stays slim; pass `--build-arg INSTALL_HEADLESS=true` to bake it in when
# the runtime flag will actually be flipped on.
#
# `xvfb` is MANDATORY, not optional: `invisible_playwright` launches Firefox
# HEADED by design (a real headless browser is trivially fingerprinted, defeating
# the stealth). Without an X server the launch aborts with "launched a headed
# browser without having a XServer running". The fetch module starts a virtual
# display (pyvirtualdisplay -> Xvfb) around each browser launch.
ARG INSTALL_HEADLESS=false
RUN if [ "$INSTALL_HEADLESS" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            libgtk-3-0 libdbus-glib-1-2 libasound2 libx11-xcb1 libxcomposite1 \
            libxdamage1 libxfixes3 libxrandr2 libxtst6 libpci3 libxcursor1 \
            libgdk-pixbuf-2.0-0 libpangocairo-1.0-0 libcairo-gobject2 fonts-liberation \
            xvfb \
        && rm -rf /var/lib/apt/lists/* ; \
    fi
# Bake the patched Firefox into the image (one-time ~100 MB download to
# $HOME/.cache/invisible-playwright/). The container runs as root (no USER
# directive here, none in compose), so $HOME is /root and this cache is found by
# the runtime uvicorn process unchanged. If a non-root USER is ever added, this
# fetch must run as that user (or set the cache dir via env) so the binary
# remains reachable at runtime.
RUN if [ "$INSTALL_HEADLESS" = "true" ]; then python -m invisible_playwright fetch ; fi

COPY services/article-fetcher/app ./app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

EXPOSE 8200

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200"]
