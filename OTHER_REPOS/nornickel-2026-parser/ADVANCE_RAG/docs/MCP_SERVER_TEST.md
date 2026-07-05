# MCP Server Test Scenarios

Test scenario specification for MCP search tools in `ADVANCE_RAG`.

## In Scope

- Functional test scenarios for MCP retrieval tools.
- Validation scenarios for input, source boundaries, and defaults.
- Error handling scenarios and contract checks.
- Integration scenarios with LangChain wrappers.

## Out Of Scope

- Performance and load testing methodology.
- Penetration testing and network security hardening.
- Indexing endpoint testing via MCP tools.

## Tool Coverage

Tools under test:

- `simple_rag`
- `sparse_rag`
- `advance_rag`
- `advance_rag_fast`
- `grep_rag`

Endpoints not exposed via MCP tools:

- `POST /api/v1/index_doc`
- `POST /api/v1/index_path`

Indexing endpoint request contract (outside MCP scope):

- `POST /api/v1/index_doc` expects `{ "path": "..." }`
- `POST /api/v1/index_path` expects `{ "path": "..." }`

## Functional Scenarios

### Basic retrieval by tool

- `simple_rag` returns results and reports effective type `dense`.
- `sparse_rag` returns results and reports effective type `sparse`.
- `advance_rag` returns reranked results and reports effective type `advance`.
- `advance_rag_fast` returns fused results and reports effective type `RRF`.
- `grep_rag` returns fuzzy matches and reports effective type `fuzzy`.

### Default behavior

- Omitted `source_subfolder` uses default `01_docling_clean00`.
- Omitted `limit` uses default `10`.
- Omitted optional fields still produce valid response schema.
- No-match query returns `200` with `results: []`.

### Language support

- English query text is accepted and processed.
- Russian query text is accepted and processed.
- Mixed English/Russian query text is accepted.

### NLTK preprocessing

- Query preprocessing executes before retrieval.
- Lemmatization can be enabled or disabled via config.
- Stemming can be enabled or disabled via config.
- Tool response remains valid regardless of preprocessing toggle state.

## Boundary And Validation Scenarios

### Source subfolder restrictions

- Allowed subfolder query succeeds.
- Non-allowed subfolder query is rejected with contract error.
- Path traversal style values are rejected.

### Input validation

- Empty `query` is rejected by contract rules.
- Unsupported tool-specific parameters are rejected.
- Invalid `limit` values are rejected.

## Error Contract Scenarios

- Invalid payload returns `400` or `422` according to validation stage.
- Missing source data returns `404`.
- Internal failure returns `500`.
- Chroma unavailable returns `503`.

## Non-Exposure Scenarios

- MCP tool list does not include indexing tools.
- MCP call attempt for `index_doc` is rejected or unavailable.
- MCP call attempt for `index_path` is rejected or unavailable.

## Schema Contract Scenarios

Validate every successful response includes:

- top-level `query`
- top-level `type`
- top-level `source_subfolder`
- top-level `results`

Validate every result item includes:

- `document_id`
- `path`
- `score`
- `content`
- `okf_meta`

Validate `okf_meta` includes required field:

- `type`

## Integration Scenarios With `examples`

- MCP client example can connect to MCP server with provided config.
- MCP client can execute all five retrieval tools.
- LangChain conversion example can wrap MCP tools and execute calls.
- Local `@tool` wrappers call REST API and mimic MCP tool behavior.
- Tool-to-type mapping is correct for all five tools.
- Local wrapper outputs keep same schema contract as MCP tools.
- Error propagation from REST API is surfaced correctly in tool response.

## Regression Scenarios

- Changes to API `v1` query contract do not silently break MCP tools.
- Changes to config default source or limit are reflected in MCP behavior.
- Changes to allowed subfolders are enforced consistently by all tools.
