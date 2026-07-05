# Step 07: Chroma Adapter Bootstrap

## Objective
Create a real Chroma adapter with collection lifecycle management.

## Test First
- Integration tests against local Chroma instance:
  - collection initialize/create.
  - upsert basic documents.
  - query returns inserted IDs.
- Failure test for unavailable Chroma dependency.

## Implement
- Add Chroma adapter abstraction with concrete implementation.
- Load collection configuration from `config.yaml`.
- Add startup validation hook for adapter readiness.

## Verify
- Run Chroma integration tests.
- Confirm readiness behavior reflects adapter availability.

## Definition of Done
- Real Chroma adapter is operational.
- Dependency failures are surfaced clearly.

## Out of Scope for This Step
- Query endpoint.
- Search mode routing.
