# Step 22: `examples` Integrations

## Objective
Provide runnable examples for MCP bridge and local LangChain tools.

## Test First
- Integration tests for `examples`:
  - MCP client connects and runs all retrieval tools.
  - MCP-to-LangChain wrapper executes tool calls.
  - local `@tool` wrappers call REST `/api/v1/query`.
  - tool mapping and output schema match MCP behavior.

## Implement
- Add `ADVANCE_RAG/examples`:
  - minimal config for MCP and API
  - MCP client example
  - LangChain-from-MCP tool wrapper example
  - local `@tool` wrappers calling REST API directly
  - simple agent usage example

## Verify
- Execute example integration tests.
- Validate docs and example defaults align with service contract.

## Definition of Done
- The unified `examples` folder is runnable and test-covered.

## Out of Scope for This Step
- Release checklist and final hardening.
