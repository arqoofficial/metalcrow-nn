# Step 08: Query Schemas and Defaults

## Objective
Define typed query request/response schemas with contract defaults.

## Test First
- Unit tests for request schema:
  - `type` default is `advance`.
  - `limit` default is `10`.
  - default source subfolder from config is applied.
  - invalid type values rejected.
- Unit tests for response schema with empty results.

## Implement
- Add Pydantic request/response models for `/api/v1/query`.
- Add enum for search type values.
- Add schema-level validation and defaults.

## Verify
- Run schema validation tests.
- Ensure no use of `@dataclass` for contract models.

## Definition of Done
- Query schemas fully typed and validated.
- Defaults match docs.

## Out of Scope for This Step
- Actual retrieval logic.
- HTTP endpoint implementation.
