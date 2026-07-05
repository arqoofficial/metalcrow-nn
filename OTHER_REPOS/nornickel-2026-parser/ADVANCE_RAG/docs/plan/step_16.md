# Step 16: Source Subfolder Policy Enforcement

## Objective
Enforce source subfolder defaults and allowlist policy.

## Test First
- Unit tests for policy:
  - default `01_docling_clean00` when omitted.
  - allowlist passes configured folders.
  - non-allowlisted folder rejected.
- Integration tests through `/api/v1/query`.

## Implement
- Centralize source subfolder resolution utility.
- Apply resolution and allowlist validation in query and indexing validation layers.

## Verify
- Run policy unit/integration tests.
- Confirm consistent behavior across endpoints.

## Definition of Done
- Source subfolder policy is deterministic and shared.

## Out of Scope for This Step
- Indexing endpoint full implementation.
