# Services Layer

Deployment and process architecture. Implements [SPECIFICATION.md](SPECIFICATION.md).

Related: [LAYER_PRESENTATION.md](LAYER_PRESENTATION.md) (API), [LAYER_DATA.md](LAYER_DATA.md) (OKF files), [LAYER_CONFIG.md](LAYER_CONFIG.md) (runtime config), [DOCLING.md](DOCLING.md) (stage-0 conversion).

---

## Runtime topology

```
                    ┌─────────────────────────────────────┐
                    │  NFS mount: SHARED/                 │
                    │  RAW_DATA/  UPLOAD_DATA/            │
                    │  00_docling_raw/  01_docling_clean00│
                    └──────────────┬──────────────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         │                         │                         │
         ▼                         ▼                         ▼
┌─────────────────┐    ┌──────────────────────┐    ┌──────────────────────────┐
│ service/main    │    │ service/raw2docling_ │    │ service/docling_raw2     │
│ (singleton)     │    │ raw (N instances)    │    │ docling_clean00 (N inst.)│
│ REST API        │    │ pdf → md             │    │ clean md → md            │
└────────┬────────┘    └──────────┬───────────┘    └────────────┬─────────────┘
         │                        │                             │
         ▼                        ▼                             ▼
┌───────────────────────┐ ┌───────────────────────┐ ┌──────────────────────────┐
│ parser:jobs:raw2...   │ │ parser:jobs:clean00   │ │ lock files on SHARED/    │
│ stage-specific queue  │ │ stage-specific queue  │ │ *.upload.lock *.worker...│
└───────────────────────┘ └───────────────────────┘ └──────────────────────────┘
```

| Component | Scaling | Role |
|-----------|---------|------|
| `service/main` | **Singleton** (exactly one instance) | User API, upload, enqueue, status |
| `service/raw2docling_raw` | Horizontal (N workers) | Stage 0: raw file → OKF `.md` in `00_docling_raw/` |
| `service/docling_raw2docling_clean00` | Horizontal (N workers) | Stage 1: clean stage-0 `.md` → `01_docling_clean00/` |
| Redis | Single instance / cluster | Separate queue per stage |
| NFS | Shared storage | All services read/write `SHARED/` |

---

## Storage: NFS

* `SHARED/` is mounted on every host via **NFS**.
* All durable state lives on NFS: raw files, OKF outputs, no local-only caches required.
* Workers **must** write atomically (write temp file + rename) to avoid partial reads on NFS.
* Never delete data; new versions add files on disk.

Git version ownership is **out of scope** for this service design.

Environment:

| Variable | Description |
|----------|-------------|
| `SHARED_ROOT` | Absolute path to `SHARED/` on NFS |
| `REDIS_URL` | Redis connection URL |

---

## Redis queue

Queues are split by stage:

- `parser:jobs:raw2docling_raw`
- `parser:jobs:docling_raw2docling_clean00`

Producers:

- `service/main` for user-triggered process and reindex.
- stage 0 workers to enqueue stage 1.

Consumers:

- `service/raw2docling_raw` reads only `parser:jobs:raw2docling_raw`.
- `service/docling_raw2docling_clean00` reads only `parser:jobs:docling_raw2docling_clean00`.

### Job message

Pydantic model: `QueueJob` in `app/queue/job.py`.

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | string | Unique job id (UUID) |
| `requested_path` | string | Path from API request |
| `resolved_path` | string | Concrete raw path selected by resolver |
| `stage` | enum | `raw2docling_raw` or `docling_raw2docling_clean00` |
| `enforce` | bool | Force reprocessing |
| `enqueued_at` | datetime | ISO 8601 UTC |

Serialization: JSON via `model_dump_json()` / `model_validate_json()`.

`resolved_path` is a concrete file key. Workers treat it as opaque and only require `os.path.exists()` truth before processing.

### Stage routing

| Queue | Consumer service | Input | Output |
|-------|------------------|-------|--------|
| `parser:jobs:raw2docling_raw` | `service/raw2docling_raw` | raw concrete file, e.g. `UPLOAD_DATA/reports/q1__v02.pdf` | `SHARED/00_docling_raw/UPLOAD_DATA/reports/q1__v02.pdf.md` |
| `parser:jobs:docling_raw2docling_clean00` | `service/docling_raw2docling_clean00` | stage-0 `.md` | `SHARED/01_docling_clean00/UPLOAD_DATA/reports/q1__v02.pdf.md` |

---

## Service: `service/main` (singleton)

**Must run as a single instance.** A second instance must refuse to start or exit.

### Responsibilities

* FastAPI — [LAYER_PRESENTATION.md](LAYER_PRESENTATION.md)
* Write uploads to `SHARED/UPLOAD_DATA/` with allocated version suffix
* Enqueue `QueueJob` to stage queues; reject with `409` when stage-0 output exists and `enforce=false`
* Derive status from files + queue presence + worker lock files (stale lock → `processing` until restart cleanup)
* Provide paginated `GET /api/v1/files/tree` view over `SHARED/` filesystem
* Never run docling or cleanup

### Singleton enforcement

On startup, acquire Redis lock:

```
KEY: parser:main:leader
VALUE: <hostname>:<pid>
TTL: 30s, renewed every 10s
```

If `SET NX` fails, log error and exit. Release lock on shutdown.

Entry point: `service/main/main.py`

---

## Lock files

Lock files are plain files and are part of runtime behavior.

| Lock type | Pattern | Usage |
|-----------|---------|-------|
| Upload allocation lock | `<raw_file_path>.upload.lock` | Protect next-version allocation |
| Worker lock | `<file_key>.worker.lock` | Signals active processing |

`clean_lock.sh` removes both lock types during full-system restart. Script is non-interactive and tolerant to missing paths.

---

## Service: `service/raw2docling_raw`

**Multiple instances allowed.** Competing consumers on the stage-specific Redis queue.

### Loop

1. `BLPOP parser:jobs:raw2docling_raw`
2. Parse `QueueJob` from `parser:jobs:raw2docling_raw`
3. Resolve input: `{SHARED_ROOT}/{resolved_path}`
4. Create worker lock file for `resolved_path`
5. If stage output exists and `enforce=false`, job should not reach worker (API returns `409`); if `enforce=true`, overwrite (last-writer-wins)
6. Run **Docling** on input via `app/workers/docling.py` ([DOCLING.md](DOCLING.md)); OCR for scanned PDF pages (default languages `en`, `ru`); reject stub/empty output
7. Build `ParserOkfFrontmatter` (stage `docling_raw`, `raw.sha256`, `pipeline.docling_version`, …)
8. Write OKF to `{SHARED_ROOT}/00_docling_raw/{resolved_path}.md`
9. Enqueue stage 1 job to `parser:jobs:docling_raw2docling_clean00`
10. Remove worker lock file

On conversion failure: record error marker under `{SHARED_ROOT}/.pipeline_errors/docling_raw/`; status API may report `failed` for that stage.

Job execution is bounded by `runtime.process_timeout_seconds`.

### I/O

| | Path |
|---|------|
| Input | Concrete raw path (simple or versioned), e.g. `SHARED/RAW_DATA/reports/q1.pdf` or `SHARED/UPLOAD_DATA/reports/q1__v02.pdf` |
| Output | `SHARED/00_docling_raw/<same-relative>.md` |

Entry point: `service/raw2docling_raw/worker.py`

---

## Service: `service/docling_raw2docling_clean00`

**Multiple instances allowed.**

### Loop

1. `BLPOP parser:jobs:docling_raw2docling_clean00`
2. Parse `QueueJob`
3. Input OKF: `{SHARED_ROOT}/00_docling_raw/{resolved_path}.md`
4. `parse_okf()` — validate YAML frontmatter via Pydantic
5. Run fast cleanup on **body** (`app/workers/cleanup.py`, based on [FAST_CLEANUP_EXAMPLE.md](FAST_CLEANUP_EXAMPLE.md))
6. Update frontmatter (`stage=docling_clean00`, `processed_at`, `pipeline.cleaner_version`, keep `raw`)
7. Write to `{SHARED_ROOT}/01_docling_clean00/{resolved_path}.md`

### I/O

| | Path |
|---|------|
| Input | `SHARED/00_docling_raw/<raw_basename>.md` |
| Output | `SHARED/01_docling_clean00/<raw_basename>.md` |

Entry point: `service/docling_raw2docling_clean00/worker.py`

---

## End-to-end flow

```
User upload (POST /files/upload)
  → main writes SHARED/UPLOAD_DATA/.../q1.pdf (first) or .../q1__vNN.ext (repeat)
  → optional: POST /files/process

main enqueues QueueJob to parser:jobs:raw2docling_raw
  → raw2docling_raw worker: raw → 00_docling_raw/...__vNN.ext.md
  → enqueues QueueJob to parser:jobs:docling_raw2docling_clean00
  → docling_raw2docling_clean00 worker: clean → 01_docling_clean00/...__vNN.ext.md

User GET /files/status → main reads NFS + queue hints
User GET /markdown?okf_path=... → main reads NFS file
```

---

## Shared code layout

```
app/
  data/           # OKF models, parse/serialize (LAYER_DATA.md)
  queue/          # QueueJob, Redis helpers
  paths.py        # Mirror path mapping, Docling/archive extension sets
  workers/
    docling.py    # Docling conversion (required)
    cleanup.py    # Stage-1 cleanup
    stage0.py     # Stage-0 job
    stage1.py     # Stage-1 job
    failure.py    # Pipeline error markers
service/
  main/
    main.py
  raw2docling_raw/
    worker.py
  docling_raw2docling_clean00/
    worker.py
```

---

## Runtime behavior notes

| Case | Behavior |
|------|----------|
| Docling error | Error marker on disk; status `failed` for that stage; see [DOCLING.md](DOCLING.md) |
| Unsupported / archive raw file | Not enqueued by reindex; process/status return `404` / upload `400` |
| Missing input file | Best effort handling; if absent at consume time, worker moves on |
| Process with existing stage-0 output | `409` when `enforce=false`; `enforce=true` enqueues and overwrites |
| Duplicate in-flight jobs | Allowed when output not on disk yet; last-writer-wins on same target path |
| Worker crash mid-job | Status stays `processing` until `clean_lock.sh` on full restart |
| Job timeout | Worker aborts after `runtime.process_timeout_seconds`; failure marker recorded |

---

## Changelog

| Date | Change |
|------|--------|
| 2026-07-03 | Required Docling conversion, failure markers, cleanup module; see [DOCLING.md](DOCLING.md) |
| 2026-07-03 | Grill session: enforce/`409` at main, stale lock status, duplicate in-flight enqueue |
| 2026-07-03 | Updated files-tree responsibility: support `offset`/`limit` pagination |
| 2026-07-03 | Added main-service responsibility for files-tree endpoint |
| 2026-07-03 | Initial services layer: NFS+git, Redis queue, three services |
| 2026-07-03 | Updated after grill session: separate queues, concrete file keys, lock file contracts |
