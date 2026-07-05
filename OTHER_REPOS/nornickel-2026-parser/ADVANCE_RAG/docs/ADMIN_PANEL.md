# Admin Panel

Operator control surface for `ADVANCE_RAG`.

## In Scope

- `panel.sh` behavior.
- `panel-docker.sh` behavior.
- Operator commands for start, stop, rerun, and indexing flows.

## Out Of Scope

- End-user UI.
- Business logic execution inside panel process.
- Replacing API endpoints with panel-only actions.

## `panel.sh` Contract

- `panel.sh` is a Typer + Rich terminal application.
- It provides service control and operational visibility.
- It is used for host-level control workflow.

## `panel-docker.sh` Contract

- `panel-docker.sh` controls behavior in Docker runtime.
- It is the operator wrapper for Docker Compose lifecycle actions.
- It is used to start, stop, and rerun service components under Docker.

## Operator Command Flows

Expected operator capabilities through panel scripts:

- start service
- stop service
- rerun service
- trigger indexing of single document
- trigger indexing of subfolder path
- inspect current runtime status (health, readiness, queue size, Chroma document count, dense embedding model)

Indexing command payload mapping:

- `index-doc` sends `{ "path": "..." }` to `POST /api/v1/index_doc`
- `index-path` sends `{ "path": "..." }` to `POST /api/v1/index_path`

Runtime visibility:

- `status` calls `GET /admin/runtime` and shows:
  - `queue.backend` — configured queue backend (`memory` or `redis`)
  - `queue.size` — pending `index_path` jobs
  - `queue.failed_count` — failed worker jobs
  - `chroma.document_count` — number of documents indexed in the active Chroma collection
  - `dense_embedding.model` — model used for dense retrieval (`all-MiniLM-L6-v2` in `cpu_local`, `text-embedding-3-small` in `openapi`)

## Operational Boundary

- Panels orchestrate and observe service behavior.
- API remains the canonical contract for query and indexing operations.
