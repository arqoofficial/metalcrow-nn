# Step 04: Health, Readiness, and Metrics Skeleton

## Objective
Expose base service observability endpoints before business logic.

## Test First
- Integration tests:
  - `GET /health` returns `200`.
  - `GET /ready` returns `200` when dependencies are reachable.
  - `GET /metrics` returns Prometheus payload.

## Implement
- Add FastAPI routers for `/health` and `/ready`.
- Expose `/metrics` using Prometheus client.
- Add minimal readiness checks for config and Chroma adapter init status.

## Verify
- Run endpoint integration tests.
- Validate response schema and status codes.

## Definition of Done
- All three endpoints are live.
- Tests pass without mocked HTTP layer.

## Out of Scope for This Step
- Query and indexing endpoints.
- Detailed tracing spans.
