# Step 05: SHARED Boundary and Path Rules

## Objective
Implement strict filesystem path handling under `SHARED`.

## Test First
- Unit tests for path normalization:
  - valid relative paths resolve inside `SHARED`.
  - traversal attempts are rejected.
  - non-allowed source subfolders are rejected.

## Implement
- Add path utility module with:
  - safe join
  - normalization
  - allowlist validation against config
- Return clear error objects for invalid paths.

## Verify
- Run path rule unit tests.
- Add integration test through a thin API validator dependency.

## Definition of Done
- All path operations are boundary-safe.
- Rejection behavior is deterministic.

## Out of Scope for This Step
- OKF parsing.
- Retrieval/indexing behavior.
