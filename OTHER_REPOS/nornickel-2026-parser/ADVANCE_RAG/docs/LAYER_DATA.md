# Data Layer

Data contract for `ADVANCE_RAG` source files, subfolder routing, and metadata response requirements.

## In Scope

- `SHARED` source subfolder contract.
- Allowed query source subfolder behavior from configuration.
- OKF metadata fields required in query responses.
- Read-only source database policy for `ADVANCE_RAG`.

## Out Of Scope

- OKF production pipeline in other services.
- Schema evolution policy for external producers.
- Non-OKF data source support.

## Source Storage Contract

- Source data is read from `SHARED`.
- Source content format is OKF on file system.
- `ADVANCE_RAG` does not create source records in `SHARED`.
- Other services are responsible for writing source records.

Known source subfolders:

- `00_docling_raw`
- `01_docling_clean00`

## Query Source Subfolder Contract

- Default query source subfolder is `01_docling_clean00`.
- Default value is defined in `config.yaml`.
- Request may override source subfolder using `source_subfolder`.
- Override must match one of allowed subfolders from `config.yaml`.
- Any folder not in the allowed list is rejected.

## Indexing Path Contract

- `POST /api/v1/index_doc` accepts only `{ "path": "..." }`.
- `POST /api/v1/index_path` accepts only `{ "path": "..." }`.
- For both endpoints, `path` must be relative to `SHARED`.
- `path` must start with one of allowed source subfolders from `config.yaml`.
- Source subfolder is derived from `path` and is not passed as a request field.

## OKF Metadata In Query Response

When a document is returned by `/api/v1/query`, response item must include `okf_meta` with the following contract.

| Field | Type | Required | Notes |
|------|------|----------|------|
| `type` | string | yes | OKF concept type |
| `title` | string | no | Document title |
| `description` | string | no | One-line summary |
| `resource` | string | no | Canonical resource URI |
| `tags` | array[string] | no | Cross-cutting tags |
| `timestamp` | string | no | ISO 8601 timestamp |
