# Step 17: `/api/v1/index_doc` Direct Indexing

## Objective
Implement direct single-document indexing endpoint.

## Test First
- Integration tests:
  - valid path indexes document and returns success response.
  - invalid path rejected.
  - disallowed source subfolder rejected.
- Test that endpoint behavior does not use queue.

## Implement
- Add request/response schemas for index_doc.
- Implement endpoint logic:
  - validate path
  - parse OKF source
  - upsert into Chroma

## Verify
- Run endpoint integration tests with real Chroma adapter.

## Definition of Done
- `/api/v1/index_doc` works end-to-end directly.

## Out of Scope for This Step
- Path-wide asynchronous indexing.
