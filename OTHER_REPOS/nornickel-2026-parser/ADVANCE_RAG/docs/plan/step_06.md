# Step 06: OKF Models and Parser Utilities

## Objective
Build typed OKF metadata parsing utilities used by query responses.

## Test First
- Unit tests for parsing OKF frontmatter:
  - valid metadata parsed correctly.
  - missing required `type` rejected.
  - malformed YAML rejected.
- Unit tests for metadata extraction to response contract.

## Implement
- Add Pydantic `BaseModel` classes for OKF metadata payload.
- Implement parser utility that reads frontmatter + body.
- Provide helper returning normalized `okf_meta` for response usage.

## Verify
- Run parser and model validation tests.
- Confirm output schema matches docs contract.

## Definition of Done
- OKF metadata extraction is typed and tested.
- Required fields enforced.

## Out of Scope for This Step
- Search ranking logic.
- API endpoint wiring.
