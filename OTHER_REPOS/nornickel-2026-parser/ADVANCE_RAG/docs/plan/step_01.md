# Step 01: uv Init and Dependencies

## Objective
Initialize Python project metadata and dependency management with `uv`.

## Test First
- Add a test that reads project metadata and asserts:
  - `uv` workflow is documented.
  - Core runtime dependencies are declared.
  - Test dependencies are declared.

## Implement
- Initialize `pyproject.toml` for `ADVANCE_RAG`.
- Define dependency groups:
  - runtime: `fastapi`, `uvicorn`, `pydantic`, `loguru`, `chromadb`, `fuzzysearch`, `nltk`, `prometheus-client`, `opentelemetry-*`
  - test: `pytest`, `pytest-asyncio`, `httpx`
- Generate and commit `uv.lock`.
- Add `make` or shell helper commands for:
  - `uv sync`
  - `uv run pytest`

## Verify
- Run `uv sync`.
- Run `uv run pytest` and ensure test collection works.

## Definition of Done
- `pyproject.toml` and `uv.lock` exist.
- `uv sync` completes.
- Test runner executes.

## Out of Scope for This Step
- Service runtime behavior.
- API routing and schemas.
