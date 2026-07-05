# ADVANCE_RAG Specification

`ADVANCE_RAG` is a FastAPI microservice that provides retrieval over corporate knowledge stored in `SHARED` as OKF files.

## In Scope

- HTTP API `v1` for retrieval and indexing operations.
- Search modes: `advance`, `fuzzy`, `dense`, `sparse`, `RRF`.
- Internal Chroma collection ownership and lifecycle.
- Read access to `SHARED` data produced by other services.
- Queue-backed path indexing flow.
- Docker and Docker Compose startup model.
- `uv` is the package manager for dependency and environment management.
- Observability with Prometheus, OpenTelemetry, and Loguru.
- Operator control through `panel.sh` and `panel-docker.sh`.

## Out Of Scope

- Writing new source OKF records into `SHARED`.
- Data ownership decisions for services outside `ADVANCE_RAG`.
- Auth model and multi-tenant policy.
- Cross-service orchestration outside this microservice boundary.

## Source Data Contract

- Source of truth for knowledge content is the file system under `SHARED`.
- OKF content is produced by other services and consumed by `ADVANCE_RAG`.
- `ADVANCE_RAG` does not create source records in `SHARED`.
- Supported source subfolders include `00_docling_raw` and `01_docling_clean00`.

## API Contract Summary

- API version is `v1`.
- `POST /api/v1/query` handles retrieval synchronously and does not use queue.
- Query default `limit` is `10` when request does not provide it.
- Query requests in English and Russian are supported.
- `POST /api/v1/index_doc` indexes one file path under `SHARED` from `{ "path": "..." }`.
- `POST /api/v1/index_path` schedules indexing for all files under an allowed folder path from `{ "path": "..." }` and uses internal queue.
- Query results that return documents must include required OKF metadata.

## Query Subfolder Rules

- Default query source subfolder is `01_docling_clean00`.
- Default is defined in `config.yaml`.
- Client may override with an explicit request parameter to target another allowed subfolder, such as `00_docling_raw`.
- Allowed query subfolders are defined in `config.yaml`.
- Access to other `SHARED` folders is not allowed.

## Chroma Contract

- Chroma is internal to `ADVANCE_RAG` and is not shared as an external datastore.
- Chroma lifecycle is managed by `ADVANCE_RAG` processes and scripts.
- Default embedding mode uses local small CPU models.
- Advanced embedding mode is enabled only when OpenAPI-compatible LLM endpoint settings are provided and selected in configuration.

## Configuration Contract

- Secrets are loaded from `.env`.
- Non-secret runtime configuration is loaded from `config.yaml`.
- NLTK query preprocessing is configurable via `config.yaml`, including lemmatization and stemming toggles.

## Data Model Contract

- Structured request, response, and internal data classes use Pydantic `BaseModel`.
- `@dataclass` is not used for these contracts.

## Layer Documentation

- API details: `LAYER_PRESENTATION.md`
- Topology and queue lifecycle: `LAYER_SERVICES.md`
- OKF metadata and source data rules: `LAYER_DATA.md`
- Config and secret boundaries: `LAYER_CONFIG.md`
- Docker and observability runtime: `LAYER_INFRASTRUCTURE.md`
- Operator controls: `ADMIN_PANEL.md`
- MCP tool contract for retrieval-only search: `MCP_SERVER.md`
- Documentation update policy: `DOCUMENTATION_REQUIREMENTS.md`
