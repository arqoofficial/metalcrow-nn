# Step 23: Final Integration Sweep and Release Checklist

## Objective
Stabilize implementation, validate contracts, and prepare release-ready state.

## Test First
- Add final end-to-end integration suite covering:
  - query modes and no-match behavior
  - indexing endpoints and queue flow
  - source subfolder allowlist enforcement
  - EN/RU query support
  - MCP retrieval tool mappings and indexing exclusion
- Add regression tests for previously fixed contract drift points.

## Implement
- Fix any remaining contract mismatches found by tests.
- Align runtime responses and error handling with docs.
- Ensure docs and examples reflect final implementation behavior.

## Verify
- Run full test matrix:
  - unit
  - integration
  - MCP integration
  - examples integration
- Run lint/type checks.

## Definition of Done
- All tests pass.
- Contracts in docs and implementation are consistent.
- Service is ready for handoff.

## Out of Scope for This Step
- New features not listed in `ADVANCE_RAG/docs`.
