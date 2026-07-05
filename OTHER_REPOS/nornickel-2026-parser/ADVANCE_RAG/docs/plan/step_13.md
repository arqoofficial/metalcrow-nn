# Step 13: Advance Mode with Reranker

## Objective
Implement `type=advance` as dense+sparse candidate union followed by reranker.

## Test First
- Unit tests for candidate union behavior.
- Unit tests for reranker invocation and ordering.
- Integration tests for `type=advance` endpoint path.

## Implement
- Build candidate set from dense and sparse outputs.
- Integrate reranker interface and concrete default implementation.
- Apply reranker output to final ranked response.

## Verify
- Run unit tests for union and reranking.
- Run full query integration tests for `advance`.

## Definition of Done
- `advance` mode behavior matches contract.

## Out of Scope for This Step
- NLTK preprocessing.
