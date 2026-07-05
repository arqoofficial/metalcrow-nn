# Stub Inventory

All items from the original audit have been implemented. This file is kept as a historical index.

## Implemented

| Area | Implementation |
|------|----------------|
| Docling conversion | **Required** core dependency; real conversion validated on `SHARED/RAW_DATA` PDFs in every test run |
| Stage-0 metadata | `docling_version()`, media types, optional git info |
| Cleanup | Full cleaner from `docs/FAST_CLEANUP_EXAMPLE.md` in `app/workers/cleanup.py` |
| Stage-1 metadata | `cleaner_version`, worker name in OKF frontmatter |
| Worker failures | Error markers in `SHARED/.pipeline_errors/`, `failed` status in API |
| Job timeout | `runtime.process_timeout_seconds` enforced in worker loops |
| Metrics | `MetricsMiddleware`, `metrics_enabled` config gate |
| OpenTelemetry | OTLP export when `OTEL_EXPORTER_OTLP_ENDPOINT` is set |
| Langfuse | SDK init when enabled and keys are present |
| Health probes | `GET /health`, `GET /ready` on main service |
| Reindex body | `ReindexRequest.enforce` forwarded to queue jobs |
| Admin panel | Redis queue depth, keyboard shortcuts, confirmations, restart hooks |
| API token | `ADMIN_PANEL_API_TOKEN` sent as Bearer token |
| Restart script | `rerun.sh` at repo root |

## Intentionally out of scope

See `docs/SPECIFICATION.md` §Out of scope (AuthN/AuthZ, archive inputs, stale lock auto-recovery, etc.).

## Regenerating SHARED artifacts

Existing OKF files under `SHARED/` may still contain old stub output. Run reindex with `enforce=true` after deploying to regenerate them.
