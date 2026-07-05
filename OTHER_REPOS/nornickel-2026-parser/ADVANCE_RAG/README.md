# ADVANCE_RAG

FastAPI microservice for retrieval over corporate knowledge stored in `SHARED` as OKF files.

## Module Layout

| Module | Responsibility |
|--------|----------------|
| `app/api` | HTTP routers, request/response schemas, FastAPI app factory |
| `app/config` | Typed configuration from `config.yaml` and `.env` secrets |
| `app/data` | OKF parsing, SHARED path utilities, source subfolder policy |
| `app/retrieval` | Search modes: dense, sparse, fuzzy, RRF, advance with reranker |
| `app/indexing` | Single-document and path-based indexing into Chroma |
| `app/queue` | Lightweight queue and worker for async path indexing |
| `app/observability` | Loguru logging, Prometheus metrics, OpenTelemetry tracing |

## Development

Use [uv](https://docs.astral.sh/uv/) for dependency management:

```bash
uv sync          # install dependencies
uv run pytest    # run tests
make sync        # alias for uv sync
make test        # alias for uv run pytest
```

Preload local retrieval assets (dense ONNX model + NLTK corpora + reranker stopwords):

```bash
bash load_model.sh
```

## Operator Scripts

- `./panel.sh` runs host-mode Typer + Rich control panel.
- `./panel-docker.sh` controls Docker Compose lifecycle and indexing actions.
- Docker image build expects local assets preloaded via `bash load_model.sh`.

Indexing endpoint payloads:

- `POST /api/v1/index_doc` accepts `{ "path": "..." }`
- `POST /api/v1/index_path` accepts `{ "path": "..." }`

## Documentation

See `docs/SPECIFICATION.md` and layer docs for API and data contracts.
