# Step 18: Lightweight Queue and Worker Runtime

## Objective
Build lightweight queue runtime used by path indexing flow.

## Test First
- Unit tests for enqueue/dequeue semantics.
- Integration tests for worker lifecycle:
  - worker picks queued jobs.
  - worker handles malformed job payload safely.

## Implement
- Add queue abstraction and local implementation.
- Add worker process loop with graceful shutdown hooks.
- Add structured logging for job lifecycle.

## Verify
- Run queue + worker integration tests.

## Definition of Done
- Queue runtime is stable and test-covered.

## Out of Scope for This Step
- `/api/v1/index_path` endpoint.
