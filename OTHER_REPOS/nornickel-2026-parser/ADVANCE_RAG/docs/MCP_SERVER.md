# MCP Server Contract

MCP server specification for search tools backed by `ADVANCE_RAG`.

## In Scope

- MCP search tools for retrieval in this project.
- Tool input and output contract.
- Mapping between MCP tools and `ADVANCE_RAG` API `v1`.
- Source-subfolder and language rules for tool calls.
- Example integration folders for MCP and LangChain tool usage.

## Out Of Scope

- MCP host runtime implementation details.
- Tooling for projects outside `ADVANCE_RAG`.

## Transport Mapping

- MCP tools call `ADVANCE_RAG` HTTP API `v1`.
- Tool behavior must remain consistent with API contracts in `LAYER_PRESENTATION.md`.

## Security Model

- Authentication is not required.
- Server is intended for internal trusted environment only.

## Search Tools

All tools below map to `POST /api/v1/query`.

## Not Available Via MCP Tools

- `POST /api/v1/index_doc` is not exposed via MCP tools.
- `POST /api/v1/index_path` is not exposed via MCP tools.
- MCP tools are retrieval-only for this project.

### `simple_rag`

- Purpose: simple semantic retrieval.
- Query type mapping: `dense`.
- Default source subfolder: `01_docling_clean00` from `config.yaml`.
- Default `limit`: `10`.

### `sparse_rag`

- Purpose: lexical sparse retrieval.
- Query type mapping: `sparse`.
- Default source subfolder: `01_docling_clean00` from `config.yaml`.
- Default `limit`: `10`.

### `advance_rag`

- Purpose: high-quality retrieval with reranking.
- Query type mapping: `advance`.
- Behavior: union of dense and sparse candidates with reranker.
- Default source subfolder: `01_docling_clean00` from `config.yaml`.
- Default `limit`: `10`.

### `advance_rag_fast`

- Purpose: faster hybrid retrieval with reduced compute.
- Query type mapping: `RRF`.
- Behavior: dense and sparse retrieval merged by Reciprocal Rank Fusion.
- Default source subfolder: `01_docling_clean00` from `config.yaml`.
- Default `limit`: `10`.

### `grep_rag`

- Purpose: approximate text lookup behavior.
- Query type mapping: `fuzzy`.
- Backend: `fuzzysearch`.
- Default source subfolder: `01_docling_clean00` from `config.yaml`.
- Default `limit`: `10`.

## Common Tool Input Schema

| Field | Type | Required | Default | Notes |
|------|------|----------|---------|------|
| `query` | string | yes | - | Query text |
| `source_subfolder` | string | no | `01_docling_clean00` | Must be in allowed config list |
| `limit` | integer | no | `10` | Max returned results |

## Common Tool Output Schema

| Field | Type | Required | Notes |
|------|------|----------|------|
| `query` | string | yes | Echoed input |
| `type` | string | yes | Effective mapped search mode |
| `source_subfolder` | string | yes | Effective source |
| `results` | array | yes | Ranked documents |

Each result item:

| Field | Type | Required | Notes |
|------|------|----------|------|
| `document_id` | string | yes | Service document id |
| `path` | string | yes | Relative path |
| `score` | number | yes | Ranking score |
| `content` | string | yes | Snippet/content |
| `okf_meta` | object | yes | Required metadata from OKF |

Language and preprocessing rules:

- English and Russian requests are supported.
- Query preprocessing uses NLTK before retrieval.
- Lemmatization and stemming are configurable via `config.yaml`.
- No-match query response is `200` with `results: []`.

## Error Contract

| Code | Meaning |
|------|------|
| `400` | Invalid input or disallowed folder |
| `404` | Not expected for normal query no-match flow |
| `422` | Validation error |
| `500` | Internal service error |
| `503` | Chroma or required dependency unavailable |

## Source Boundary Rules

- Allowed source subfolders are defined in `config.yaml`.
- Default query source subfolder is `01_docling_clean00`.
- Any folder outside the allowed list is rejected.
- Tool calls must never bypass this boundary.

## Folder `examples`

Purpose: unified examples folder for both MCP bridge and local LangChain tools.

Suggested contents:

- `examples/config.yaml`
- `examples/mcp_client_example.py`
- `examples/langchain_from_mcp_example.py`
- `examples/local_tools.py`
- `examples/langchain_agent_example.py`

Minimal `examples/config.yaml`:

```yaml
mcp:
  server_url: http://127.0.0.1:8120
  timeout_sec: 20
api:
  base_url: http://127.0.0.1:8114/api/v1
  timeout_sec: 20
advancerag:
  default_source_subfolder: 01_docling_clean00
  default_limit: 10
```

Usage intent:

- `mcp_client_example.py` shows raw MCP calls to `simple_rag`, `sparse_rag`, `advance_rag`, `advance_rag_fast`, `grep_rag`.
- `langchain_from_mcp_example.py` shows wrapper conversion from MCP tools to LangChain tool objects.
- `local_tools.py` provides local LangChain `@tool` wrappers that call REST API directly.
- `langchain_agent_example.py` demonstrates local tool usage.

Tool mapping in `examples/local_tools.py`:

- `@tool simple_rag` calls `/query` with `type=dense`
- `@tool sparse_rag` calls `/query` with `type=sparse`
- `@tool advance_rag` calls `/query` with `type=advance`
- `@tool advance_rag_fast` calls `/query` with `type=RRF`
- `@tool grep_rag` calls `/query` with `type=fuzzy`
