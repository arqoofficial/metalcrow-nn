# Step 02: Config Loader and Validation

## Objective
Implement strict configuration loading from `config.yaml` and `.env`.

## Test First
- Unit tests for config parsing:
  - valid config loads.
  - missing required fields fail fast.
  - default query settings apply (`type=advance`, `limit=10`, `source_subfolder=01_docling_clean00`).
  - allowed subfolder list enforcement config exists.
- Unit test that data contracts are Pydantic `BaseModel`.

## Implement
- Add Pydantic models for full config schema.
- Load non-secret runtime config from `config.yaml`.
- Load secrets from `.env`.
- Validate and return typed config object.
- Expose single `get_settings()` entrypoint.

## Verify
- Run unit tests for valid/invalid config paths.
- Confirm startup abort behavior on invalid config.

## Definition of Done
- Config is fully typed and validated.
- Invalid config fails deterministically.
- Tests pass.

## Out of Scope for This Step
- Endpoint logic.
- Retrieval/indexing implementation.
