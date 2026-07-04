# Presentation Layer

HTTP API for parser service clients. Implements [SPECIFICATION.md](SPECIFICATION.md).

- Framework: FastAPI
- Base path: `/api/v1`
- Auth: none

## Path handling contract

### Accepted path forms

Most endpoints accept:

- Logical path: `reports/q1.pdf`
- Simple concrete raw path: `RAW_DATA/reports/q1.pdf`, `UPLOAD_DATA/reports/q1.pdf`
- Versioned concrete raw path: `UPLOAD_DATA/reports/q1__v02.pdf`
- Concrete OKF path: `00_docling_raw/RAW_DATA/reports/q1.pdf.md`

Upload endpoint accepts logical path only.

### Exact-path resolution

For data-returning APIs:

1. If request starts with `UPLOAD_DATA/` or `RAW_DATA/`: use that exact on-disk path.
2. If request is a logical path: probe `UPLOAD_DATA/<path>` then `RAW_DATA/<path>` (exact filenames only).
3. If no matching file exists: return `404`.

No latest-version fallback. Sibling `__vNN` files are independent keys.

If request sends a version token without source prefix (e.g. `reports/q1__v01.pdf`), return `400`.

## Status values

Runtime values:

- `pending`
- `queued`
- `processing`
- `done`
- `failed`

`overall_status` is the worst stage state (failure ranks below processing).

Failure markers under `SHARED/.pipeline_errors/<stage>/` cause that stage to report `failed` until a successful write clears the marker.

## Endpoints

### `POST /api/v1/files/upload`

Uploads by logical path.

Request: `multipart/form-data`

| Field | Type | Required |
|------|------|----------|
| `file` | file | yes |
| `path` | string (logical) | yes |

Response: `202 Accepted` with `resolved_path`.

Notes:

- Upload never blocks user request.
- **First upload** for a logical key writes simple filename: `UPLOAD_DATA/<logical-path>`.
- **Repeat upload** when any file exists for that key allocates next `__vNN` suffix.

### `POST /api/v1/files/process`

Enqueue processing for file.

Request body:

| Field | Type | Required |
|------|------|----------|
| `path` | logical or concrete path | yes |
| `enforce` | bool | no |

Behavior:

- Resolve **exact** file on disk; logical paths try `UPLOAD_DATA/` then `RAW_DATA/`.
- If file missing: `404`.
- If stage-0 OKF output already exists on disk and `enforce=false`: `409 Conflict` — client must pass `enforce=true` to reprocess.
- If stage-0 output does not exist yet but work is already `queued` or `processing`: enqueue another job anyway (duplicates allowed).
- If `enforce=true`: enqueue even when stage-0 output exists; workers overwrite (last-writer-wins).

Response: `202 Accepted`, includes `requested_path` and `resolved_path`.

### `GET /api/v1/files/status`

Return stage statuses for target file.

Query:

| Param | Type | Required |
|------|------|----------|
| `path` | logical or concrete raw/OKF path | yes |

Response includes:

- `requested_path`
- `resolved_path`
- `is_final`
- per-stage status and concrete `okf_path`

For logical path requests, status reflects the exact matched file (simple name or versioned key).
For concrete path requests, status is anchored to that exact on-disk path.

Stale worker lock (worker crashed mid-job, lock file remains, no stage output, no failure marker): stage stays `processing` indefinitely until operator runs `clean_lock.sh` during full-system restart.

### `GET /api/v1/markdown`

Download markdown content.

Query:

| Param | Type | Required |
|------|------|----------|
| `okf_path` | logical or concrete path | yes |

Behavior reads the **exact** OKF file under `SHARED/`. Raw paths map to stage-0 OKF for the same resolved raw file only (no stage-1 fallback).

Response: `200 OK` `text/markdown`.

Headers:

- `X-Requested-Path`
- `X-Resolved-Path`

### `GET /api/v1/statistics`

Statistics over **all** concrete raw files (simple and versioned).

Response:

- `total_raw_files` — every parseable raw file under `UPLOAD_DATA/` and `RAW_DATA/`
- `stage0_done` / `stage1_done` — how many of those files have OKF outputs
- `coverage_ratio` — `stage1_done / total_raw_files`

### `GET /api/v1/files/tree`

Return filesystem tree for all parser data under `SHARED/`.

`SHARED` is implicit root and must not be provided in request values.

Query:

| Param | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `root` | string | no | `` (root) | Optional subtree path relative to `SHARED` (for example: `RAW_DATA`, `UPLOAD_DATA/reports`, `01_docling_clean00/UPLOAD_DATA`) |
| `max_depth` | int | no | `6` | Max nested level to include (`<= 10`) |
| `include_files` | bool | no | `true` | Include file nodes in output |
| `include_dirs` | bool | no | `true` | Include directory nodes in output |
| `offset` | int | no | `0` | Pagination offset for direct children of resolved root |
| `limit` | int | no | `200` | Max number of direct children returned for resolved root (`<= 1000`) |

Response: `200 OK` with tree structure.

Subtree contract:

- `root` is always interpreted relative to `SHARED`.
- `root` must not start with `SHARED` or `/`.
- If `root` is empty or omitted, service returns tree from `SHARED`.
- Recoverable malformed roots are normalized and returned with `200` + warnings.
- Any request that attempts to resolve outside `SHARED` must return `400`.

Visibility and traversal rules:

- Hidden files/directories are excluded.
- Lock files are always excluded (no override).
- Symlinks are not followed.

Examples:

- `GET /api/v1/files/tree` -> full tree from `SHARED`
- `GET /api/v1/files/tree?root=UPLOAD_DATA` -> only upload subtree
- `GET /api/v1/files/tree?root=01_docling_clean00/UPLOAD_DATA` -> specific stage/source subtree

Pagination contract:

- `offset`/`limit` apply to direct children of the resolved root node only.
- Child ordering must be deterministic (lexicographic by node name).
- Nested children (below first level) follow `max_depth` and are not independently paginated.
- This is a known limitation of v1 contract.

Response shape (example):

```json
{
  "requested_root": "",
  "resolved_root": "",
  "offset": 0,
  "limit": 200,
  "has_more": false,
  "next_offset": null,
  "warnings": [
    {
      "code": "ROOT_NORMALIZED",
      "message": "Collapsed repeated path separators."
    }
  ],
  "generated_at": "2026-07-03T03:00:00Z",
  "tree": {
    "name": "SHARED",
    "type": "dir",
    "children": [
      {
        "name": "UPLOAD_DATA",
        "type": "dir",
        "children": [
          {"name": "reports", "type": "dir", "children": []}
        ]
      }
    ]
  }
}
```

Errors:

- `400` invalid `root`, `max_depth`, `offset`, or `limit`
- `400` request attempts to resolve outside `SHARED`
- `404` requested subtree does not exist

### `POST /api/v1/reindex`

Reindex scheduling endpoint. Enqueues stage-0 jobs for **every** Docling-eligible raw file under `UPLOAD_DATA/` and `RAW_DATA/` (archives and unsupported extensions skipped).

Request body:

| Field | Type | Required | Default |
|------|------|----------|---------|
| `enforce` | bool | no | `false` |

When `enforce=false`, files that already have stage-0 OKF output are skipped. When `enforce=true`, jobs are enqueued even when stage-0 output exists (workers overwrite).

Response: `202 Accepted` with `enqueued` count.

### `GET /health` and `GET /ready`

Liveness and readiness probes on the **main service root** (not under `/api/v1`):

| Endpoint | Behavior |
|----------|----------|
| `GET /health` | Always `200` with `{"status": "ok"}` when process is up |
| `GET /ready` | `200` with `{"status": "ready"}` when Redis ping succeeds; otherwise error |

Swagger tag: **Health**. Prometheus export: `GET /metrics` (tag **Observability**).

## Error responses

| Code | When |
|------|------|
| `400` | Invalid path or inconsistent selectors |
| `404` | Exact file not found on disk |
| `409` | Stage output already exists and `enforce=false` on process |
| `422` | Validation errors |
| `500` | Internal error |

## Pydantic model sketch

Suggested module: `app/presentation/schemas.py`

```python
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ProcessingStatus(str, Enum):
    pending = "pending"
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class ProcessRequest(BaseModel):
    path: str
    enforce: bool = False


class ProcessResponse(BaseModel):
    requested_path: str
    resolved_path: str
    enforce: bool
    status: ProcessingStatus
    queued_at: datetime


class ReindexRequest(BaseModel):
    enforce: bool = False


class ReindexResponse(BaseModel):
    enqueued: int


class StageStatus(BaseModel):
    stage: str
    status: ProcessingStatus
    okf_path: Optional[str] = None


class FileStatusResponse(BaseModel):
    requested_path: str
    resolved_path: str
    is_final: bool
    overall_status: ProcessingStatus
    stages: list[StageStatus]


class FileTreeNode(BaseModel):
    name: str
    type: str  # "dir" | "file"
    children: list["FileTreeNode"] = []


class FileTreeResponse(BaseModel):
    requested_root: str
    resolved_root: str
    offset: int
    limit: int
    has_more: bool
    next_offset: Optional[int] = None
    warnings: list[dict[str, str]] = []
    generated_at: datetime
    tree: FileTreeNode
```

## Changelog

| Date | Change |
|------|--------|
| 2026-07-03 | OpenAPI tags (Files, Content, Browse, …); rich Swagger descriptions |
| 2026-07-03 | `failed` status from `.pipeline_errors/`; reindex `enforce`; `/health` and `/ready` |
| 2026-07-03 | Exact-path contract: no version fallback; reindex enqueues all files; markdown reads exact OKF path |
| 2026-07-03 | Grill session: process `409` when stage-0 output exists and `enforce=false`; stale lock → `processing` |
| 2026-07-03 | Finalized tree contract from grilling: SHARED boundary `400`, bounds, warnings, hidden/lock exclusion, no symlink traversal |
| 2026-07-03 | Added pagination (`offset`, `limit`, `has_more`, `next_offset`) to `GET /api/v1/files/tree` |
| 2026-07-03 | Added `GET /api/v1/files/tree` endpoint for full filesystem tree browsing |
| 2026-07-03 | Consolidated grill session rules for status payloads and endpoint behavior |
