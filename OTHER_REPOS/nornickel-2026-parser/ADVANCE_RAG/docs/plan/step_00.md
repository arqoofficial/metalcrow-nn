# Step 00: Project Bootstrap and Layout

## Objective
Create the base `ADVANCE_RAG` service skeleton with clear module boundaries and test directories.

## Test First
- Add a structure smoke test that asserts required directories exist.
- Required directories:
  - `ADVANCE_RAG/app`
  - `ADVANCE_RAG/app/api`
  - `ADVANCE_RAG/app/config`
  - `ADVANCE_RAG/app/data`
  - `ADVANCE_RAG/app/retrieval`
  - `ADVANCE_RAG/app/indexing`
  - `ADVANCE_RAG/app/queue`
  - `ADVANCE_RAG/app/observability`
  - `ADVANCE_RAG/tests/unit`
  - `ADVANCE_RAG/tests/integration`

## Implement
- Create the required directory structure.
- Add empty `__init__.py` files in Python packages.
- Add a short `README.md` in `ADVANCE_RAG` describing module responsibilities.

## Verify
- Run structure smoke test.
- Confirm imports from `app.*` packages do not fail.

## Definition of Done
- Directory layout exists exactly as specified.
- Structure smoke test passes.
- Package imports resolve.

## Out of Scope for This Step
- Dependency installation.
- Runtime configuration loading.
- API endpoint implementation.
