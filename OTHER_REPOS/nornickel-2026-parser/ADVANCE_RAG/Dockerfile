FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
RUN chmod +x /app/load_model.sh

# Use preloaded project assets only (no network fetch during runtime).
RUN /app/load_model.sh --no-download

EXPOSE 8115 8120

CMD ["uv", "run", "uvicorn", "app.main:create_api_app", "--factory", "--host", "0.0.0.0", "--port", "8115"]
