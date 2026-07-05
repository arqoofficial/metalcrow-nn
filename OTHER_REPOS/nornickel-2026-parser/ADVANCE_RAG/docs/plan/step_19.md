# Step 19: `/api/v1/index_path` Async Flow

## Objective
Implement path-based indexing endpoint backed by queue worker.

## Test First
- Integration tests:
  - endpoint accepts request and returns `202` with `job_id`.
  - queued job processes all eligible docs in subfolder.
  - disallowed subfolder rejected.

## Implement
- Add `/api/v1/index_path` schemas and endpoint.
- Implement subfolder scanning and enqueue behavior.
- Implement worker handler to process queued path jobs and upsert documents.

## Verify
- Run async flow integration tests end-to-end.
- Confirm query endpoint can retrieve newly indexed documents.

## Definition of Done
- `/api/v1/index_path` async contract is fully working.

## Out of Scope for This Step
- MCP tool exposure.
