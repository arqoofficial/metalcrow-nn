# Testing Strategy

Test plan for `ADVANCE_RAG` service contracts.

## In Scope

- Unit tests for request validation, query mode dispatch, ranking, and response shaping.
- Unit tests for queue boundary behavior and config-driven source rules.
- Integration tests for API contracts, queue worker flow, and Chroma interaction.
- Contract checks for required OKF metadata in query responses.

## Out Of Scope

- Load testing and performance benchmarking methodology.
- Security penetration testing process.
- End-to-end tests for external services outside `ADVANCE_RAG`.

## Unit Tests

### Query request validation

- Omitted `type` resolves to `advance`.
- Omitted `limit` resolves to `10`.
- Invalid `type` value is rejected.
- Invalid `source_subfolder` is rejected when not in config allowlist.
- Invalid `limit` values are rejected based on validator rules.
- English query input is accepted.
- Russian query input is accepted.

### Query mode dispatch

- `fuzzy` path calls fuzzy search adapter.
- `dense` path calls dense retriever.
- `sparse` path calls sparse retriever.
- `RRF` path calls dense and sparse retrievers and fusion logic.
- `advance` path uses dense and sparse candidate union and reranker.
- NLTK preprocessing executes before search dispatch.

### Ranking behavior

- RRF output order is deterministic for known ranked inputs.
- Advance reranker output is deterministic for known candidate set.
- Empty candidate set returns empty `results`.

### Response shaping and metadata

- Every returned document includes `okf_meta`.
- `okf_meta.type` is always present.
- Response includes effective `type`, `source_subfolder`, and requested `query`.

### Index request and queue boundaries

- `/api/v1/query` does not enqueue jobs.
- `/api/v1/index_doc` follows direct indexing path from `{ "path": "..." }`.
- `/api/v1/index_path` enqueues job payload from `{ "path": "..." }`.
- Paths outside `SHARED` are rejected.

### Config and Chroma mode

- Config loader reads `.env` and `config.yaml` correctly.
- Missing required config fields fail startup validation.
- Chroma mode resolves to local CPU mode by default.
- Chroma mode switches to OpenAPI-compatible endpoint mode when configured.
- NLTK lemmatization toggle from config is applied.
- NLTK stemming toggle from config is applied.

## Integration Tests

### API contract coverage

- `POST /api/v1/query` succeeds for `advance`, `fuzzy`, `dense`, `sparse`, and `RRF`.
- `POST /api/v1/query` uses `limit=10` when omitted.
- `POST /api/v1/query` returns `200` with `results: []` when no documents match.
- `POST /api/v1/query` succeeds with English request text.
- `POST /api/v1/query` succeeds with Russian request text.
- `POST /api/v1/index_doc` indexes a valid file path.
- `POST /api/v1/index_path` returns accepted response with `job_id`.
- `POST /api/v1/index_path` rejects a file path that is not a directory.

### Source subfolder policy

- Query default source is `01_docling_clean00` when request omits `source_subfolder`.
- Query override to `00_docling_raw` succeeds when allowed in config.
- Query override to non-allowed folder is rejected.

### Metadata contract

- Query result payload includes required OKF metadata.
- Metadata values match source OKF frontmatter for indexed document.

### Queue worker flow

- `/api/v1/index_path` enqueues job.
- Worker consumes job and indexes documents to Chroma.
- Indexed documents become retrievable through `/api/v1/query`.

### Chroma lifecycle and failure behavior

- Service starts with available Chroma collection.
- Re-indexing same document follows defined overwrite or upsert behavior.
- Chroma unavailability maps to documented dependency error response.

### Observability integration

- Prometheus metrics endpoint is reachable and exposes service metrics.
- OpenTelemetry spans are emitted for query and indexing flows.
- Loguru records include endpoint and processing context.

## Recommended Fixtures

- Temporary `SHARED` test directory with:
  - `00_docling_raw`
  - `01_docling_clean00`
- Minimal OKF files with valid frontmatter and body.
- Config fixture with:
  - allowed source subfolder list
  - default source `01_docling_clean00`
  - default query limit `10`
  - NLTK preprocessing options for lemmatization and stemming
- Chroma test collection fixture with clean setup and teardown.
- Queue fixture for in-memory or test Redis backend.
