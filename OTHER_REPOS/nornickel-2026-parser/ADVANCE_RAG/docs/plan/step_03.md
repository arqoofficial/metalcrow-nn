# Step 03: Logging Baseline and Startup Wiring

## Objective
Establish application startup with structured Loguru logging.

## Test First
- Unit tests for logger setup:
  - JSON log format in runtime mode.
  - required context fields can be injected.
- Integration test for app startup:
  - startup succeeds with valid config.
  - startup fails with invalid config.

## Implement
- Add `create_logger()` and request/job context helpers.
- Wire logger into app factory startup path.
- Log effective runtime config summary without secrets.

## Verify
- Run startup integration tests.
- Inspect logs for required fields and formatting.

## Definition of Done
- Logger initialization is centralized.
- Startup path logs deterministic metadata.
- Tests pass.

## Out of Scope for This Step
- Health endpoints.
- Metrics and tracing instrumentation.
