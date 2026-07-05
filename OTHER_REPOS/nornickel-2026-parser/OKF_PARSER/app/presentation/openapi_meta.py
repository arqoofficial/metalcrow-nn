"""OpenAPI metadata shared by the main FastAPI application."""

from __future__ import annotations

TAG_FILES = "Files"
TAG_CONTENT = "Content"
TAG_BROWSE = "Browse"
TAG_STATISTICS = "Statistics"
TAG_OPERATIONS = "Operations"
TAG_HEALTH = "Health"
TAG_OBSERVABILITY = "Observability"
TAG_INTERNAL = "Internal"

OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": TAG_FILES,
        "description": (
            "Upload raw documents and enqueue or inspect per-file pipeline processing. "
            "Paths may be logical (`reports/q1.pdf`) or concrete (`RAW_DATA/...`)."
        ),
    },
    {
        "name": TAG_CONTENT,
        "description": "Download OKF markdown for an exact resolved path under `SHARED/`.",
    },
    {
        "name": TAG_BROWSE,
        "description": "Paginated filesystem tree of parser storage rooted at `SHARED/`.",
    },
    {
        "name": TAG_STATISTICS,
        "description": "Coverage metrics across all Docling-eligible raw files on disk.",
    },
    {
        "name": TAG_OPERATIONS,
        "description": "Bulk maintenance actions such as full reindex.",
    },
    {
        "name": TAG_HEALTH,
        "description": "Liveness and readiness probes for orchestration and the admin panel.",
    },
    {
        "name": TAG_OBSERVABILITY,
        "description": "Prometheus metrics export (gated by `observability.metrics_enabled`).",
    },
    {
        "name": TAG_INTERNAL,
        "description": "Development helpers for path validation and HTTP error-shape checks.",
    },
]

API_DESCRIPTION = """
File-first document parser: raw files are converted through **Docling** into
Open Knowledge Format (OKF) markdown under `SHARED/`.

## Pipeline

| Stage | Folder | Input |
|-------|--------|-------|
| 0 `docling_raw` | `00_docling_raw/` | Raw PDF, Office, HTML, … |
| 1 `docling_clean00` | `01_docling_clean00/` | Stage-0 OKF body cleanup |

Redis carries jobs only; the filesystem is the source of truth.

## Path rules

- **Logical path** — client-facing key without source prefix, e.g. `reports/q1.pdf`.
- **Concrete path** — path relative to `SHARED/`, e.g. `RAW_DATA/reports/q1.pdf`.
- Repeat uploads allocate `__vNN` suffixes; reads always resolve **exact** on-disk keys (no latest-version fallback).
- Archives (`.zip`, `.tar`, …) are rejected for upload and excluded from Docling reindex.

## Status values

`pending` → `queued` → `processing` → `done`, or `failed` when a pipeline error marker exists under `.pipeline_errors/`.

## Further reading

Repository docs: `docs/LAYER_PRESENTATION.md`, `docs/SPECIFICATION.md`, `docs/DOCLING.md`.
""".strip()
