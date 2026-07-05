# Step 09: First Working Slice - Dense Query End-to-End

## Objective
Deliver first fully working vertical slice: `/api/v1/query` with dense retrieval.

## Test First
- Integration tests:
  - request with type `dense` returns ranked results.
  - no-match returns `200` with `results: []`.
  - `okf_meta` included for every result.
- Test default behavior when `type` omitted and temporarily mapped to dense path until later steps.

## Implement
- Add `/api/v1/query` endpoint.
- Wire request validation, path/source checks, dense retrieval call, response assembly.
- Read OKF metadata for returned documents.

## Verify
- Run endpoint integration tests against real Chroma adapter.
- Validate response schema and no-match behavior.

## Definition of Done
- `/api/v1/query` works with dense retrieval path.
- No-match contract implemented.

## Out of Scope for This Step
- Sparse, fuzzy, RRF, and reranker logic.
