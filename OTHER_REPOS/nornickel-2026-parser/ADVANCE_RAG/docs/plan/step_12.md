# Step 12: RRF Fusion

## Objective
Implement `type=RRF` by combining dense and sparse ranked lists.

## Test First
- Unit tests for RRF formula and rank merging.
- Unit tests for deterministic tie-break behavior.
- Integration test for `type=RRF` path.

## Implement
- Add RRF combiner utility.
- For `RRF`, execute dense and sparse retrieval, then fuse results.
- Return fused ranking through common response builder.

## Verify
- Run RRF unit tests with fixed fixtures.
- Run endpoint integration tests.

## Definition of Done
- `type=RRF` is deterministic and tested.

## Out of Scope for This Step
- `advance` reranker mode.
