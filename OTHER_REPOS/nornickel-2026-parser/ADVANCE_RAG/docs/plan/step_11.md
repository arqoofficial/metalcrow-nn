# Step 11: Fuzzy Retrieval Path

## Objective
Implement fuzzy retrieval mode using `fuzzysearch`.

## Test First
- Unit tests for fuzzy matching over indexed text snippets.
- Integration tests for `type=fuzzy` endpoint behavior.
- Test no-match contract for fuzzy path.

## Implement
- Add fuzzy retriever adapter backed by `fuzzysearch`.
- Route query type `fuzzy` to fuzzy retriever.
- Ensure score normalization compatible with response contract.

## Verify
- Run fuzzy unit and integration tests.
- Confirm result schema consistency with other modes.

## Definition of Done
- `type=fuzzy` is production-ready and tested.

## Out of Scope for This Step
- RRF fusion and reranker.
