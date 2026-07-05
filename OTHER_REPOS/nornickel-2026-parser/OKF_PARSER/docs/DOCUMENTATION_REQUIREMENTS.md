# Documentation Requirements

Requirements for user-facing and operator-facing documentation in this project.
Code changes that affect public behavior must update docs **before merge** unless the change is explicitly internal.

Related: [SPECIFICATION.md](SPECIFICATION.md), [LAYER_PRESENTATION.md](LAYER_PRESENTATION.md), [IMPLEMENTATION_NOTES.md](../IMPLEMENTATION_NOTES.md).

---

## 1. Audiences

| Audience | Primary surfaces | Goal |
|----------|------------------|------|
| **API consumer** | Swagger UI (`/docs`), ReDoc (`/redoc`), OpenAPI JSON (`/openapi.json`) | Integrate upload, process, status, markdown without reading Python |
| **Operator / admin** | [ADMIN_PANEL.md](ADMIN_PANEL.md), `./panel.sh`, `./reindex.sh` | Run, monitor, and recover the pipeline |
| **Contributor** | `docs/LAYER_*.md`, `docs/plan/`, `IMPLEMENTATION_NOTES.md` | Implement and review against contracts |

User documentation = **Swagger + layer docs written for humans**, not inline code comments alone.

---

## 2. Two documentation layers (must stay in sync)

### 2.1 Interactive API docs (Swagger / OpenAPI)

**Source of truth in code:**

| Module | Responsibility |
|--------|----------------|
| `app/presentation/openapi_meta.py` | App description, tag names, tag group descriptions |
| `app/presentation/schemas.py` | Request/response models with `Field(description=..., examples=...)` |
| `app/presentation/router.py` | Route `tags`, `summary`, `description`, `responses` |
| `app/presentation/health_router.py` | Root health probes |
| `service/main/main.py` | `FastAPI(...)` metadata, non-versioned routes (`/metrics`) |

**Rule:** Every public HTTP route must appear under a **named tag**, never the Swagger “default” group.

### 2.2 Contract docs (markdown in `docs/`)

| Doc | When to update |
|-----|----------------|
| [LAYER_PRESENTATION.md](LAYER_PRESENTATION.md) | Any endpoint, status code, query param, or response shape change |
| [SPECIFICATION.md](SPECIFICATION.md) | Scope, path rules, pipeline, status model, operational semantics |
| [DOCLING.md](DOCLING.md) | Conversion, OCR, supported extensions, validation |
| [ADMIN_PANEL.md](ADMIN_PANEL.md) | Panel commands, config, keyboard shortcuts, probes |
| [LAYER_CONFIG.md](LAYER_CONFIG.md) | New config keys or env vars |

**Rule:** If Swagger and `LAYER_PRESENTATION.md` disagree, **fix both** in the same change set. Prefer `LAYER_PRESENTATION.md` for normative contract; Swagger for discoverability and examples.

---

## 3. OpenAPI tag groups (required layout)

Use constants from `app/presentation/openapi_meta.py`. Do not invent ad-hoc tag names.

| Tag | Routes | Notes |
|-----|--------|-------|
| **Files** | `POST /api/v1/files/upload`, `POST /api/v1/files/process`, `GET /api/v1/files/status` | Ingestion and per-file pipeline |
| **Content** | `GET /api/v1/markdown` | OKF markdown download |
| **Browse** | `GET /api/v1/files/tree` | SHARED tree |
| **Statistics** | `GET /api/v1/statistics` | Coverage metrics |
| **Operations** | `POST /api/v1/reindex` | Bulk maintenance |
| **Health** | `GET /health`, `GET /ready` | Root probes, not under `/api/v1` |
| **Observability** | `GET /metrics` | Prometheus export |
| **Internal** | `GET /api/v1/validate/path`, `GET /api/v1/health/error/{code}` | Dev/contract helpers only |

Adding a new public endpoint **requires** choosing an existing tag or adding a new tag in `openapi_meta.py` with a paragraph description.

---

## 4. Per-endpoint requirements (Swagger)

Every route handler must define:

1. **`tags`** — one of the groups above.
2. **`summary`** — short imperative phrase (≤ ~10 words), e.g. “Enqueue pipeline processing”.
3. **`description`** — markdown-friendly text covering:
   - what the endpoint does,
   - path resolution rules if relevant,
   - non-obvious status codes (`409`, `404`, etc.).
4. **`responses`** — at minimum document error codes the handler can return; use `ErrorResponse` model where applicable.
5. **Query / form / path params** — use `Query(...)`, `Form(...)`, or `Path(...)` with `description` and `examples` when not obvious from the schema.

New endpoints must not ship with bare `@router.get("/foo")` and no metadata.

---

## 5. Schema requirements (Pydantic)

All public request/response models in `app/presentation/schemas.py`:

- Every field: `Field(..., description="...")`.
- Prefer `examples=` on fields that accept paths or enums.
- Enums (`ProcessingStatus`, etc.): document all values in field or enum docstring.
- Optional: `model_config = ConfigDict(json_schema_extra={"examples": [...]})` for whole-model examples.

Schemas are part of user documentation; empty fields in Swagger are a doc defect.

---

## 6. App-level OpenAPI metadata

`service/main/main.py` must keep:

- `title`, `version`, `description` (from `API_DESCRIPTION`),
- `openapi_tags=OPENAPI_TAGS`,
- sensible `swagger_ui_parameters` (e.g. `docExpansion: list`, `filter: true`).

Bump `version` on breaking API changes.

---

## 7. Content standards for descriptions

Write for someone who has **not** cloned the repo:

- Use **logical** vs **concrete** path terminology consistently (see [SPECIFICATION.md](SPECIFICATION.md)).
- Mention `enforce` behavior wherever process/reindex can conflict with existing output.
- State that reads use **exact** on-disk paths (no latest-version fallback).
- Link to repo docs in app description; do not duplicate entire layer docs in Swagger.
- Use present tense, complete sentences.
- Avoid internal module paths in user-facing text unless helpful (`SHARED/`, `UPLOAD_DATA/` are OK).

---

## 8. Operator documentation

Changes affecting `./panel.sh`, health probes, or reindex must update [ADMIN_PANEL.md](ADMIN_PANEL.md):

- CLI commands and flags,
- config keys under `admin_panel:`,
- keyboard shortcuts in interactive mode,
- which API URLs the panel calls.

---

## 9. Changelog discipline

Any doc listed in §2.2 that you edit must append a row to its **Changelog** table:

```markdown
| Date | Change |
|------|--------|
| YYYY-MM-DD | Short description of what changed and why |
```

---

## 10. Review checklist (PR / self-review)

- [ ] New/changed route has tag, summary, description, responses.
- [ ] Schemas have field descriptions (and examples where useful).
- [ ] `LAYER_PRESENTATION.md` matches behavior and status codes.
- [ ] Swagger shows **no** endpoints under “default”.
- [ ] Health/metrics paths documented at correct URL prefix.
- [ ] Admin/operator docs updated if CLI or probes changed.
- [ ] Relevant changelog tables updated.
- [ ] `uv run pytest tests/api -q` passes.

---

## 11. Out of scope for user docs

Do not expose in Swagger as production APIs without review:

- Debug-only routes (keep under **Internal** tag).
- Undocumented env vars or config overrides.
- Implementation details (Redis key names, lock file internals) unless operators need them — those belong in [LAYER_SERVICES.md](LAYER_SERVICES.md).

---

## Changelog

| Date | Change |
|------|--------|
| 2026-07-03 | Initial documentation requirements (OpenAPI tags, schemas, layer doc sync) |
