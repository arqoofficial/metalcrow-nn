# Services Layer

Runtime service topology and process responsibilities for `ADVANCE_RAG`.

## In Scope

- Service topology with API process and index queue worker.
- Chroma ownership and lifecycle responsibilities.
- Queue boundaries for synchronous and asynchronous paths.
- Indexing lifecycle for single-document and path-based indexing.

## Out Of Scope

- HTTP schema details.
- Infrastructure monitoring backend setup.
- Non-ADVANCE_RAG services.

## Topology

`ADVANCE_RAG` runs as isolated components:

- API process serving `/api/v1/*`.
- Internal index queue worker processing path indexing jobs.
- Internal Chroma datastore used only by this service.
- Shared file input from `SHARED`.

## Queue Boundaries

- `/api/v1/query` executes in request cycle and never enters queue.
- `/api/v1/index_doc` is direct request-driven indexing.
- `/api/v1/index_path` enqueues job and returns asynchronously.

## Chroma Ownership

- Chroma collection is owned by `ADVANCE_RAG`.
- No external service writes into this Chroma collection directly.
- API process and worker are the only valid Chroma writers.
- Query flow reads from Chroma and does not mutate source `SHARED` records.

## Chroma Lifecycle

- Startup loads Chroma settings from `config.yaml`.
- Service validates collection availability before accepting traffic.
- Indexing operations upsert documents into owned collection.
- Restart/rerun scripts keep lifecycle operationally manageable.

## Index Lifecycle

- `/api/v1/index_doc` validates file path from `{ "path": "..." }` and indexes target document immediately.
- `/api/v1/index_path` validates folder path from `{ "path": "..." }`, resolves source subfolder, and enqueues job.
- Worker scans eligible files, transforms to index payload, and upserts in Chroma.
- Queue worker status and failures are surfaced through logs and telemetry.
