# Step 20: Observability Hardening

## Objective
Add production-grade metrics and tracing around query and indexing flows.

## Test First
- Integration tests asserting metrics are emitted for:
  - query requests
  - index_doc operations
  - index_path jobs
- Tests asserting OpenTelemetry spans are created for API and worker operations.

## Implement
- Instrument query and indexing paths with Prometheus counters/histograms.
- Add OpenTelemetry spans and context propagation across queue jobs.
- Ensure logging includes correlation IDs.

## Verify
- Run observability integration tests.
- Validate `/metrics` payload includes new families.

## Definition of Done
- Metrics and traces cover critical execution paths.

## Out of Scope for This Step
- MCP layer behavior.
