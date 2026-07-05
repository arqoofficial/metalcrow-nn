# Step 15: English and Russian Query Support

## Objective
Validate bilingual query support for English and Russian input.

## Test First
- Integration tests with English queries.
- Integration tests with Russian queries.
- Integration tests with mixed-language queries.

## Implement
- Ensure tokenizer/preprocessor/retrievers handle UTF-8 text safely.
- Fix language handling gaps in preprocessing or retrieval adapters.

## Verify
- Run bilingual integration suite across all query modes.

## Definition of Done
- EN/RU queries are accepted and return valid responses.

## Out of Scope for This Step
- Additional language support.
