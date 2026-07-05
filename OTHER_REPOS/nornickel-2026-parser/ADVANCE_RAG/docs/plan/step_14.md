# Step 14: NLTK Preprocessing Pipeline

## Objective
Add configurable NLTK preprocessing before retrieval dispatch.

## Test First
- Unit tests for preprocessing pipeline order.
- Unit tests for config toggles:
  - lemmatization on/off
  - stemming on/off
- Integration tests showing query mode output changes when toggles change.

## Implement
- Add preprocessing module for tokenization/normalization.
- Apply preprocessing before retrieval mode dispatch.
- Read and enforce preprocessing flags from `config.yaml`.

## Verify
- Run preprocessing unit tests with fixed text fixtures.
- Run query integration tests with preprocessing enabled/disabled.

## Definition of Done
- Preprocessing is configurable and exercised in query path.

## Out of Scope for This Step
- Language-specific validation beyond EN/RU support checks.
