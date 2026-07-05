# Presentation Layer

HTTP API contract for `ADVANCE_RAG`. Implements `SPECIFICATION.md`.

- Framework: FastAPI
- API version: `v1`
- Base path: `/api/v1`

## In Scope

- Exact contract for `POST /api/v1/query`.
- Exact contract for `POST /api/v1/index_doc`.
- Exact contract for `POST /api/v1/index_path`.
- Request and response schema tables.
- Status and error code contract.
- Queue boundary behavior per endpoint.

## Out Of Scope

- Internal Chroma storage layout.
- Worker process internals.
- External service orchestration.

## Queue Boundary Contract

- `POST /api/v1/query` is synchronous and does not use queue.
- `POST /api/v1/index_doc` is direct indexing request.
- `POST /api/v1/index_path` is asynchronous and uses internal queue.

## `POST /api/v1/query`

Search indexed documents derived from allowed `SHARED` subfolders.

### Request schema

| Field | Type | Required | Default | Notes |
|------|------|----------|---------|------|
| `query` | string | yes | - | User query text |
| `type` | string | no | `advance` | Allowed: `advance`, `fuzzy`, `dense`, `sparse`, `RRF` |
| `source_subfolder` | string | no | from `config.yaml` | Must be one of allowed subfolders from config |
| `limit` | integer | no | `10` | Max documents returned |

### Search mode behavior

- Query processing supports English and Russian requests.
- Query text is preprocessed with NLTK before search according to configured preprocessing options.
- `advance`: union of dense and sparse candidates, then reranker produces final ranking.
- `fuzzy`: fuzzy matching backed by `fuzzysearch`.
- `dense`: dense/vector retrieval.
- `sparse`: sparse retrieval.
- `RRF`: dense and sparse ranked lists merged with Reciprocal Rank Fusion.

No-match behavior:

- If query completes but no documents match, response is `200` with `results: []`.

### Response schema

| Field | Type | Required | Notes |
|------|------|----------|------|
| `query` | string | yes | Echoed query |
| `type` | string | yes | Effective search type |
| `source_subfolder` | string | yes | Effective source subfolder |
| `results` | array | yes | Ranked document results |

Result item schema:

| Field | Type | Required | Notes |
|------|------|----------|------|
| `document_id` | string | yes | Service-level document id |
| `path` | string | yes | Relative path inside selected subfolder |
| `score` | number | yes | Rank score |
| `content` | string | yes | Returned snippet or content |
| `okf_meta` | object | yes | Required OKF metadata payload |

Required `okf_meta` fields:

| Field | Type | Required |
|------|------|----------|
| `type` | string | yes |
| `title` | string | no |
| `description` | string | no |
| `resource` | string | no |
| `tags` | array[string] | no |
| `timestamp` | string | no |

### `curl` example

```bash
curl -X POST "http://127.0.0.1:8115/api/v1/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "nickel production forecast",
    "type": "advance",
    "source_subfolder": "01_docling_clean00",
    "limit": 10
  }'
```

## `POST /api/v1/index_doc`

Index one file from `SHARED` into Chroma.

### Request schema

| Field | Type | Required | Notes |
|------|------|----------|------|
| `path` | string | yes | File path under `SHARED` |

### Response schema

| Field | Type | Required | Notes |
|------|------|----------|------|
| `status` | string | yes | `indexed` |
| `path` | string | yes | Effective indexed file path |

### `curl` example

```bash
curl -X POST "http://127.0.0.1:8115/api/v1/index_doc" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "01_docling_clean00/reports/q1_report.okf.md"
  }'
```

## `POST /api/v1/index_path`

Schedule indexing for all eligible files under one allowed folder path.

### Request schema

| Field | Type | Required | Notes |
|------|------|----------|------|
| `path` | string | yes | Folder path under `SHARED`, starting with an allowed source subfolder |

### Response schema

| Field | Type | Required | Notes |
|------|------|----------|------|
| `status` | string | yes | `accepted` |
| `job_id` | string | yes | Queue job id |
| `path` | string | yes | Effective folder path |

### `curl` example

```bash
curl -X POST "http://127.0.0.1:8115/api/v1/index_path" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "01_docling_clean00/reports"
  }'
```

## Status And Error Contract

### Success status codes

| Endpoint | Status | Meaning |
|------|------|------|
| `POST /api/v1/query` | `200` | Query executed |
| `POST /api/v1/index_doc` | `200` or `202` | Direct index accepted or completed |
| `POST /api/v1/index_path` | `202` | Async indexing accepted |

### Error status codes

| Code | Meaning |
|------|------|
| `400` | Invalid request payload or invalid subfolder |
| `404` | Requested file/path not found for indexing endpoints |
| `422` | Schema validation error |
| `500` | Internal processing error |
| `503` | Chroma or required dependency unavailable |
