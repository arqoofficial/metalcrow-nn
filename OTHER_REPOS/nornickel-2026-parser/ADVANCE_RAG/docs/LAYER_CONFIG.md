# Configuration Layer

Configuration contract for `ADVANCE_RAG`.

## In Scope

- Secret and non-secret configuration boundary.
- Chroma runtime mode switching contract.
- API version and source-subfolder defaults from config.

## Out Of Scope

- Deployment secret manager choice.
- Non-ADVANCE_RAG service configuration.
- UI-level configuration.

## Configuration Sources

- `.env` stores secrets.
- `config.yaml` stores non-secret runtime configuration.

## Secret Contract

Secrets belong in `.env`, such as:

- OpenAPI-compatible LLM endpoint credentials.
- Any API keys or tokens used by integrations.
- Credentials embedded in connection URLs.

`.env` is runtime input and must not be treated as non-secret config documentation.

## Non-Secret Contract

`config.yaml` contains runtime behavior, including:

- API version and route settings.
- `SHARED` root path.
- Allowed source subfolders for query/index operations.
- Default source subfolder for query.
- Default query limit.
- Queue settings for path indexing.
- Chroma mode settings.
- NLTK query preprocessing settings.
- Observability toggles and endpoints.

## Query Source Settings

`config.yaml` must define:

- default source subfolder: `01_docling_clean00`
- default query limit: `10`
- allowed source subfolders list
- query preprocessing settings for NLTK lemmatization and stemming

Requests targeting folders outside this allowed list must fail.

## Chroma Mode Switch

- CPU local mode is default and uses local small models.
- Advanced mode is enabled only when OpenAPI-compatible LLM endpoint configuration is present and active.
- Mode selection is controlled by `config.yaml`.

## Query Preprocessing Settings

- Query preprocessing uses NLTK before retrieval.
- Preprocessing is configurable in `config.yaml`.
- Configuration controls enable or disable lemmatization, stemming, and related preprocessing behavior.
- Configuration must support English and Russian query processing.
