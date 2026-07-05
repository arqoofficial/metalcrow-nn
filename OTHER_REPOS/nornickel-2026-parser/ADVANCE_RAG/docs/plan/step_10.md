# Step 10: Sparse Retrieval Path

## Objective
Add sparse retrieval implementation and route it through `/api/v1/query`.

## Test First
- Integration tests for `type=sparse` returning results.
- Deterministic ranking unit tests for sparse scorer.
- No-match `200` with empty results for sparse path.

## Implement
- Add sparse retriever module.
- Route query type `sparse` to sparse retriever.
- Reuse shared response builder with metadata extraction.

## Verify
- Run sparse integration and ranking tests.
- Confirm dense path remains unaffected.

## Definition of Done
- `type=sparse` behavior is implemented and tested.

## Out of Scope for This Step
- Fuzzy retrieval and hybrid fusion.
