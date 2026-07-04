# Specification

## Overview

Service parses raw files into Open Knowledge Format (OKF markdown) through a multi-stage pipeline.

The system is file-first:

- `SHARED/` filesystem is source of truth.
- Redis is transport for jobs only.
- No metadata database.

Related docs:

- [LAYER_PRESENTATION.md](LAYER_PRESENTATION.md)
- [LAYER_SERVICES.md](LAYER_SERVICES.md)
- [LAYER_DATA.md](LAYER_DATA.md)
- [LAYER_CONFIG.md](LAYER_CONFIG.md)
- [ADMIN_PANEL.md](ADMIN_PANEL.md)
- [LAYER_INFRASTRUCTURE.md](LAYER_INFRASTRUCTURE.md)
- [DOCLING.md](DOCLING.md)

## Scope

### In scope

- Upload file via REST API.
- Process file through pipeline stages.
- Return status and markdown content.
- Return statistics.
- Return tree of files and directories in data storage.
- Reindex data.

### Out of scope

- AuthN/AuthZ.
- Deduplication and consistency recovery workflows.
- Orphan artifact management.
- Stale lock auto-recovery during normal runtime.
- Archive inputs (`zip`, `rar`, `tar`, etc.) — stored on disk but **not** sent through Docling (see [DOCLING.md](DOCLING.md)).

## Docling (required)

Stage-0 conversion **must** use the Docling library. Stub or placeholder markdown is invalid.

See [DOCLING.md](DOCLING.md) for:

- supported file types and archive exclusions,
- OCR configuration,
- output validation rules,
- testing against `SHARED/RAW_DATA`,
- regenerating stale OKF files.

### OCR languages

Stage-0 PDF conversion uses Docling with OCR enabled by default for scanned pages. Default OCR languages: **English (`en`)** and **Russian (`ru`)**, configured in `config.yaml`:

```yaml
pipeline:
  docling:
    ocr_enabled: true
    ocr_languages: [en, ru]
```

Text-native PDFs and office formats (DOCX, XLSX, …) use embedded text; OCR applies when a page has no text layer.

## Storage and folders

`SHARED` is mounted on all services.

| Path | Purpose |
|------|---------|
| `SHARED/RAW_DATA/` | Bootstrap data (developer-managed) |
| `SHARED/UPLOAD_DATA/` | User uploads |
| `SHARED/00_docling_raw/` | Stage 0 OKF outputs |
| `SHARED/01_docling_clean00/` | Stage 1 OKF outputs |

## File identity and versioning

### Simple filenames (primary on-disk form)

All API endpoints accept **simple filenames** without a version token:

- Bootstrap / direct storage: `RAW_DATA/Доклады/report.pdf`
- First upload: `UPLOAD_DATA/reports/q1.pdf`
- OKF mirror: `00_docling_raw/RAW_DATA/Доклады/report.pdf.md`

**Resolution rules (exact-path contract):**

- **Direct concrete request** (`RAW_DATA/.../report.pdf`, `UPLOAD_DATA/.../q1__v02.pdf`): use that **exact** file on disk; `404` if missing.
- **Logical request** (`reports/q1.pdf`, `Доклады/report.pdf`): probe exact paths `UPLOAD_DATA/<path>` then `RAW_DATA/<path>`; `404` if neither exists.
- **No version fallback**: requesting `q1__v01.pdf` when only `q1__v03.pdf` exists returns `404`.

### Logical and concrete paths

- Logical path: `reports/q1.pdf`
- Simple concrete raw path: `UPLOAD_DATA/reports/q1.pdf` or `RAW_DATA/reports/q1.pdf`
- Versioned concrete raw path: `UPLOAD_DATA/reports/q1__v02.pdf`
- Concrete OKF path (stage 0): `00_docling_raw/UPLOAD_DATA/reports/q1.pdf.md` or `.../q1__v02.pdf.md`
- Concrete OKF path (stage 1): `01_docling_clean00/UPLOAD_DATA/reports/q1.pdf.md`

### Version format

- Version token is monotonic `vNN` (numeric compare, variable width supported).
- Versioned raw filename pattern: `<stem>__vNN.<ext>` (used when a simple file already exists).
- OKF filename pattern: `<raw_basename>.md` (mirrors the resolved raw path basename).

### Upload behavior

- Client uploads by **logical path only** (no source prefix, no `__vNN` in request).
- If no file exists yet for that logical key under `UPLOAD_DATA/`: write **simple name** `UPLOAD_DATA/<logical-path>`.
- If a file already exists (simple and/or versioned): allocate next `__vNN` suffix and write versioned file.
- New upload never returns `409`; each call creates a new file.

### Source resolution

When resolving by logical path without explicit source:

1. `UPLOAD_DATA` first
2. `RAW_DATA` second

### Data retention

- Do not delete artifacts in normal operation.
- Historical versions remain on disk.

## Pipeline

| Stage | Id | Input | Output folder |
|------|----|-------|---------------|
| 0 | `docling_raw` | raw file | `SHARED/00_docling_raw/` |
| 1 | `docling_clean00` | stage 0 OKF | `SHARED/01_docling_clean00/` |

Stages run sequentially; success of stage 0 enqueues stage 1.

## Job queues

- Separate Redis queue per stage.
- `parser:jobs:raw2docling_raw`
- `parser:jobs:docling_raw2docling_clean00`

Workers consume only their stage queue.

## Status model

Status is derived from files + queue + worker lock files.

Runtime statuses:

- `pending`
- `queued`
- `processing`
- `done`
- `failed`

Rules:

- If requested file exists on disk (exact match): use it; `resolved_path` equals the on-disk key.
- If no exact match: `404`.
- `overall_status` is worst stage state (failure ranks below processing).
- Pipeline failure marker on disk (`.pipeline_errors/<stage>/`) → stage reports `failed` until output is written successfully (marker cleared on success).
- Stale worker lock (crash mid-job, no stage output, no failure marker): stage reports `processing` until `clean_lock.sh` removes the lock during full-system restart.

## Process and enforce

`POST /files/process` accepts optional `enforce` (default `false`).

- Stage-0 OKF output already on disk and `enforce=false`: return `409 Conflict`; client must pass `enforce=true` to reprocess.
- Stage-0 output not on disk but job already `queued` or `processing`: enqueue duplicate job anyway.
- `enforce=true`: enqueue regardless of existing output; workers overwrite (last-writer-wins).

## Read behavior for data-returning endpoints

For endpoints that return data (`status`, `markdown`, similar):

1. Resolve the **exact** requested path on disk.
2. For logical paths without a source prefix: try `UPLOAD_DATA/<path>` then `RAW_DATA/<path>`.
3. For markdown with a raw path (concrete or logical): map to stage-0 OKF for that same resolved raw file only.
4. For markdown with a concrete OKF path: read that exact file under `SHARED/` (no stage fallback chain).
5. If no match: `404`.

## API contract summary

| Endpoint | Contract |
|----------|----------|
| Upload | Accept logical path, create new version |
| Process | Accept logical or concrete path; **exact** file only; `409` if stage-0 output exists and `enforce=false` |
| Status | Accept logical or concrete path; return requested and resolved paths (exact match) |
| Markdown | Return content from **exact** OKF path on disk |
| Statistics | Count all raw files (simple and versioned); stage coverage per file |
| Files tree | Return storage tree from implicit `SHARED` root or selected relative subtree, with root-level `offset`/`limit`, warnings, and strict `SHARED` boundary |
| Reindex | Enqueue **every** Docling-eligible raw file under `UPLOAD_DATA/` and `RAW_DATA/` (archives and unsupported types skipped) |

If logical and concrete selectors are both provided and inconsistent, return `400`.

## Statistics

- `total_raw_files` counts every concrete raw file on disk (simple names and `__vNN` versions).
- `stage0_done` / `stage1_done` count per-file OKF outputs; `coverage_ratio = stage1_done / total_raw_files`.

## Locking and restart scripts

Lock files are files only (never directories).

- Upload allocation lock format: `<raw_file_path>.upload.lock`.
- Worker runtime lock format: `<raw_or_okf_key>.worker.lock`.
- `clean_lock.sh` is non-interactive and removes both lock types during full-system restart.

Missing files during cleanup are tolerated.

## Operational scripts

- `reindex.sh` runs server-side reindex flow and triggers processing for data set according to reindex policy.
- Script is intended for admin/operator usage on server environments.

## Non-functional assumptions

- Best-effort processing.
- No consistency guarantees under races/crashes.
- Last-writer-wins when `enforce=true` or duplicate in-flight jobs write the same target.

## Changelog

| Date | Change |
|------|--------|
| 2026-07-03 | Docling required; archive exclusion; `failed` status; see [DOCLING.md](DOCLING.md) |
| 2026-07-03 | Grill session: process enforce/`409` rules, reindex `strategy`, stale lock → `processing` |
| 2026-07-03 | Finalized files-tree constraints: `limit <= 1000`, `max_depth <= 10`, hide hidden/lock files, no symlink traversal, `400` outside `SHARED` |
| 2026-07-03 | Added `reindex.sh` operational script to specification |
| 2026-07-03 | Files-tree endpoint now accepts subtree requests relative to implicit `SHARED` root |
| 2026-07-03 | Added pagination support (`offset`, `limit`) for files-tree endpoint |
| 2026-07-03 | Added files-tree API endpoint to main service contract |
| 2026-07-03 | Consolidated grill session decisions: file versioning, queues, status, locks, API fallback rules |
