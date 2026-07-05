# Step 21: MCP Retrieval Tools

## Objective
Implement retrieval-only MCP tool layer.

## Test First
- Integration tests for MCP tools:
  - `simple_rag` maps to `type=dense`
  - `sparse_rag` maps to `type=sparse`
  - `advance_rag` maps to `type=advance`
  - `advance_rag_fast` maps to `type=RRF`
  - `grep_rag` maps to `type=fuzzy`
- Test indexing tools are not exposed in MCP tool registry.

## Implement
- Build MCP server exposing only retrieval tools.
- Reuse `/api/v1/query` contract internally.
- Enforce no-match behavior as `200` with empty results.

## Verify
- Run MCP integration tests.
- Confirm retrieval-only tool list.

## Definition of Done
- MCP tools are implemented per contract and indexing is excluded.

## Out of Scope for This Step
- LangChain example integrations.
