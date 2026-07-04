# Implementation Notes

Single source of implementation truth for the nornickel-2026-parser project.
Code must not relax these contracts without updating the authoritative docs first.

## Coding rules

- All structured data across module/process boundaries: **Pydantic `BaseModel` only** (see `.cursor/rules/pydantic-basemodel.mdc`).
- API schemas: `app/presentation/schemas.py` unless a layer-specific module is justified.
- No dataclasses for DTOs, queue payloads, or config.

## Contract docs (authoritative)

| Doc | Purpose |
|-----|---------|
| `docs/SPECIFICATION.md` | System scope, storage, versioning, pipeline, status |
| `docs/LAYER_PRESENTATION.md` | REST API contracts and response schemas |
| `docs/LAYER_SERVICES.md` | Service responsibilities, queues, locks, singleton |
| `docs/LAYER_DATA.md` | OKF models and parser data layer |
| `docs/LAYER_CONFIG.md` | Configuration schema and load order |
| `docs/ADMIN_PANEL.md` | Admin panel behavior |
| `docs/DOCLING.md` | Stage-0 Docling conversion (required) |
| `docs/DOCUMENTATION_REQUIREMENTS.md` | User-facing doc standards (Swagger + operator docs) |
| `docs/LAYER_INFRASTRUCTURE.md` | Docker, observability, deployment |

## Path naming

### Simple filenames (default)

| Artifact | Pattern | Example |
|----------|---------|---------|
| Raw file (bootstrap / first upload) | `<stem>.<ext>` | `RAW_DATA/reports/q1.pdf`, `UPLOAD_DATA/reports/q1.pdf` |
| OKF stage 0 | `<raw_basename>.md` | `00_docling_raw/RAW_DATA/reports/q1.pdf.md` |
| OKF stage 1 | `<raw_basename>.md` | `01_docling_clean00/RAW_DATA/reports/q1.pdf.md` |

All API endpoints accept simple concrete paths and logical paths without `__vNN`.

### Upload-managed versions (subsequent uploads)

| Artifact | Pattern | Example |
|----------|---------|---------|
| Raw file (repeat upload) | `<stem>__vNN.<ext>` | `UPLOAD_DATA/reports/q1__v01.pdf` |
| OKF stage 0 | `<raw_basename>.md` | `00_docling_raw/UPLOAD_DATA/reports/q1__v01.pdf.md` |

- First upload for a logical key writes a **simple filename**.
- Repeat upload when any file exists for that key allocates next `__vNN`.
- Version token: suffix `__vNN` where `NN` is compared numerically (`v2` < `v10`).
- Logical path: client-facing path without source prefix or version token, e.g. `reports/q1.pdf`.
- Concrete path: path relative to `SHARED/` including source; may be simple or versioned.
- Logical key: source + directory + stem + extension, without `__vNN` (used for upload version scanning only).

## Exact-path resolution

`resolve_exact_raw_path` in `app/services/path_resolution.py`:

1. Concrete raw or OKF request ? exact on-disk file.
2. Logical request ? try `UPLOAD_DATA/<path>` then `RAW_DATA/<path>`.
3. No match ? `404`.

Markdown reads exact OKF paths; raw requests map to stage-0 OKF for the same resolved raw file only.

Reindex enqueues every Docling-eligible raw file (archives skipped). `ReindexRequest.enforce` controls whether existing stage-0 outputs are re-enqueued.

## Queue names

| Stage | Queue name |
|-------|------------|
| raw -> docling_raw | `parser:jobs:raw2docling_raw` |
| docling_raw -> docling_clean00 | `parser:jobs:docling_raw2docling_clean00` |

Workers consume only their stage-specific queue.

## QueueJob fields

Pydantic model in `app/queue/job.py`; JSON via `model_dump_json()` / `model_validate_json()`.

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | string | Unique job id (UUID) |
| `requested_path` | string | Path from API request |
| `resolved_path` | string | Concrete raw path selected by resolver |
| `stage` | enum | `raw2docling_raw` or `docling_raw2docling_clean00` |
| `enforce` | bool | Force reprocessing |
| `enqueued_at` | datetime | ISO 8601 UTC |

## Lock patterns

| Lock type | Pattern | Usage |
|-----------|---------|-------|
| Upload allocation | `<path>.upload.lock` | Held while allocating next `__vNN` and writing raw bytes |
| Worker runtime | `<resolved_path>.worker.lock` | Created before work; removed on completion |

Lock files are plain files (never directories). `clean_lock.sh` at repo root removes both types during full-system restart (non-interactive, tolerant to missing files).

## Main singleton leader lock

| Property | Value |
|----------|-------|
| Redis key | `parser:main:leader` |
| Value | `<hostname>:<pid>` |
| TTL | 30s |
| Renew interval | 10s |

Second main instance must refuse to start (`SET NX` failure -> log and exit). Release lock on shutdown.

## Tree endpoint policy

`GET /api/v1/files/tree` - see `docs/LAYER_PRESENTATION.md` and Decision Lock below.

- `SHARED` is implicit root; `root` query param is relative to `SHARED`.
- Recoverable malformed roots: normalize -> `200` + warnings.
- Pagination: `offset`/`limit` apply to direct children of resolved root only.
- Nested children obey `max_depth` but are not independently paginated (v1 limitation).
- Response root node: `name: "SHARED"`, `type: "dir"`.

## Status derivation order

Per stage, evaluate in order:

1. **Failure marker** ù `.pipeline_errors/<stage>/` JSON exists ? `failed`
2. **File exists** ù stage output on disk ? `done` (clears failure marker on successful write)
3. **Queue** ù job present in stage queue ? `queued`
4. **Worker lock** ù `{resolved_path}.worker.lock` exists ? `processing`
5. **Default** ù none of the above ? `pending`

Special cases:

- Stale worker lock (crash mid-job, no stage output, no failure marker): stage reports `processing` until `clean_lock.sh` removes the lock during full-system restart.
- `overall_status` is worst stage state (`failed` ranks below `processing`).

## Decision Lock

Non-negotiable rules; code must not relax without a doc update first.

| Rule | Contract |
|------|----------|
| Hidden dotfiles | Excluded from tree (names starting with `.`) |
| Lock files | Always hidden (`*.upload.lock`, `*.worker.lock`); no override flag |
| Symlinks | Do not follow; report symlink nodes as files without descending |
| Outside `SHARED` | Path escape -> HTTP `400` |
| `limit` bound | `limit <= 1000` |
| `max_depth` bound | `max_depth <= 10` |

## Step execution order

Implement strictly in order; **do not skip steps**:

| Step | File | Topic |
|------|------|-------|
| 00 | `docs/plan/step_00.md` | Implementation baseline (this document) |
| 01 | `docs/plan/step_01.md` | Configuration system |
| 02 | `docs/plan/step_02.md` | Path utilities and versioning |
| 03 | `docs/plan/step_03.md` | Queue job model and Redis queue |
| 04 | `docs/plan/step_04.md` | Core API endpoints |
| 05 | `docs/plan/step_05.md` | Tree endpoint |
| 06 | `docs/plan/step_06.md` | Worker pipeline |
| 07 | `docs/plan/step_07.md` | Status derivation |
| 08 | `docs/plan/step_08.md` | Reindex |
| 09 | `docs/plan/step_09.md` | Orchestration |
| 10 | `docs/plan/step_10.md` | Integration suite |
| 11 | `docs/plan/step_11.md` | Final verification |

Never proceed to the next step while tests for the current step fail.

## Development setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
uv run pytest -q
```

| Script | Command |
|--------|---------|
| Admin panel | `./panel.sh` (falls back to `config/local.yaml`) or `uv run -m admin_panel run` |
| Reindex | `./reindex.sh` |
| Tests | `uv run pytest -q` |

## Operational scripts

| Script | Purpose |
|--------|---------|
| `clean_lock.sh` | Remove all `*.upload.lock` and `*.worker.lock` under `SHARED_ROOT` |
| `reindex.sh` | Call `POST /api/v1/reindex` using configured API base URL |
| `panel.sh` | Launch admin panel (`run` by default); config fallback to `config/local.yaml` |
| `rerun.sh` | Restart hooks for Docker services (when enabled in admin panel config) |
| `scripts/smoke_api.sh` | Operator smoke checks for statistics, reindex, tree |

## Known partial-code drift

Most step-plan stubs are implemented. Remaining drift should be tracked in issues, not this table ù update docs when behavior changes.

| Area | Notes |
|------|-------|
| Docling | Required; see `docs/DOCLING.md` |
| Failure markers | `SHARED/.pipeline_errors/` drives `failed` status |
| Stale OKF on disk | Pre-Docling runs may show `docling_version: stub`; reindex with `enforce=true` to regenerate |
