# Data Layer — OKF Format

On-disk format for Open Knowledge Format (OKF) files. Implements [SPECIFICATION.md](SPECIFICATION.md).

OKF files follow the official **[OKF v0.1](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)** standard. Strict external definitions: [OKF_STANDARD_EXTERNAL.md](OKF_STANDARD_EXTERNAL.md). This project adds producer-defined extension fields for pipeline metadata.

Related: [LAYER_PRESENTATION.md](LAYER_PRESENTATION.md) (API), [LAYER_SERVICES.md](LAYER_SERVICES.md) (workers), [DOCLING.md](DOCLING.md) (stage-0 body), pipeline folders `SHARED/00_docling_raw/`, `SHARED/01_docling_clean00/`.

Implementation: `app/data/okf_standard.py`, `app/data/okf_parser.py`, `app/data/okf_io.py`.

---

## OKF v0.1 standard (strict)

Per [OKF SPEC §4.1](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md#41-frontmatter):

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `type` | **yes** | string | Non-empty concept kind |
| `title` | no | string | Display name |
| `description` | no | string | One-line summary |
| `resource` | no | string | Canonical URI of underlying asset |
| `tags` | no | list[string] | Cross-cutting tags |
| `timestamp` | no | ISO 8601 datetime | Last meaningful change |

**Pydantic model:** `OkfFrontmatterStandard` (`extra="forbid"` — no fields outside the standard).

Conformance (§9): every concept `.md` file has parseable YAML frontmatter with non-empty `type`.

---

## Project extension (mixin)

Parser pipeline metadata is added via `ParserOkfExtensionMixin`, combined into `ParserOkfFrontmatter`:

```python
class ParserOkfFrontmatter(OkfFrontmatterStandard, ParserOkfExtensionMixin):
    ...
```

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `raw` | yes | object | Source raw file identity |
| `stage` | yes | object | Pipeline stage that wrote this file |
| `processed_at` | yes | ISO 8601 datetime | When this OKF was written |
| `pipeline` | no | object | Tool versions |
| `git` | no | object | Git state of raw at processing time |

### `raw`

| Field | Required | Description |
|-------|----------|-------------|
| `path` | yes | Concrete raw path key, e.g. `reports/2024/q1__v02.pdf` |
| `source` | yes | `UPLOAD_DATA` or `RAW_DATA` |
| `absolute_path` | yes | Under `SHARED/` |
| `sha256` | yes | Lowercase hex SHA-256 of raw bytes |
| `media_type` | no | MIME type |
| `size_bytes` | no | Raw file size |

### `stage`

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | `docling_raw` or `docling_clean00` |
| `folder` | yes | e.g. `00_docling_raw` |
| `sequence` | no | `0` or `1` |

### Project `type` value

All parser-produced concepts use:

```yaml
type: Parsed Document
```

---

## File layout

```
┌─────────────────────────────────────┐
│  YAML frontmatter (OKF §4.1)        │  ← OKF standard + project mixin
├─────────────────────────────────────┤
│  Markdown body                      │  ← docling / cleanup output
└─────────────────────────────────────┘
```

### Syntax

* Delimiter: `---` on its own line (OKF §4).
* Encoding: UTF-8, line endings LF.
* Frontmatter must be the first content in the file.

---

## Full example (stage 1)

```markdown
---
type: Parsed Document
title: CM_07_09
description: Parsed journal article from RAW_DATA/journals/CM_07_09__v02.pdf
resource: okf://RAW_DATA/journals/CM_07_09__v02.pdf
tags: [journal, parsed]
timestamp: "2026-07-03T11:42:00Z"
raw:
  path: journals/CM_07_09__v02.pdf
  source: RAW_DATA
  absolute_path: RAW_DATA/journals/CM_07_09__v02.pdf
  sha256: "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae"
  media_type: application/pdf
  size_bytes: 1843200
stage:
  id: docling_clean00
  folder: 01_docling_clean00
  sequence: 1
processed_at: "2026-07-03T11:42:00Z"
pipeline:
  docling_version: "2.14.0"
  cleaner_version: "1.0.0"
  worker: docling-clean00-worker
git:
  commit: "a1b2c3d4"
  version_label: "v1"
---
# Цветные металлы

Cleaned article text…
```

---

## YAML parsing and validation

Use `app/data/okf_io.py`. Flow:

1. `split_frontmatter(text)` — separate YAML block and body.
2. `yaml.safe_load(yaml_block)` — parse YAML to dict.
3. `ParserOkfFrontmatter.model_validate(data)` — **Pydantic validates all fields**.

```python
from app.data.okf_io import parse_okf, serialize_okf

doc = parse_okf(text)          # ParserOkfDocument
md = serialize_okf(doc)        # round-trip markdown
```

For foreign OKF bundles (standard fields only + unknown extensions):

```python
from app.data.okf_io import parse_okf_standard

doc = parse_okf_standard(text)  # OkfDocument, OkfFrontmatterStandard
```

Invalid frontmatter raises `OkfFormatError` wrapping Pydantic `ValidationError`.

---

## Stage rules

### Stage 0 — `docling_raw`

* Set OKF standard fields (`type`, `title`, `description`, `resource`, `timestamp`).
* Write `raw`, `stage`, `processed_at`, `pipeline.docling_version` (installed Docling package version, not `stub`).
* Body = Docling markdown; must pass substantive validation (see [DOCLING.md](DOCLING.md)).

### Stage 1 — `docling_clean00`

* Preserve `raw` and OKF standard fields; update `timestamp`, `stage`, `processed_at`.
* Clean **body** only; set `pipeline.cleaner_version` (`CLEANER_VERSION` in `app/workers/cleanup.py`).

Failed stages leave JSON markers under `SHARED/.pipeline_errors/<stage>/`; they do not produce OKF output.

---

## Path mapping

| Raw | Stage 0 OKF | Stage 1 OKF |
|-----|-------------|-------------|
| `SHARED/RAW_DATA/reports/q1.pdf` | `SHARED/00_docling_raw/RAW_DATA/reports/q1.pdf.md` | `SHARED/01_docling_clean00/RAW_DATA/reports/q1.pdf.md` |
| `SHARED/UPLOAD_DATA/reports/2024/q1__v02.pdf` | `SHARED/00_docling_raw/UPLOAD_DATA/reports/2024/q1__v02.pdf.md` | `SHARED/01_docling_clean00/UPLOAD_DATA/reports/2024/q1__v02.pdf.md` |

---

## File identity on disk

Simple filenames are the default form. All API endpoints accept them.

- Simple raw: `<stem>.<ext>` under `RAW_DATA/` or `UPLOAD_DATA/`
- OKF mirror: `<raw_relative_path>.md` under stage folders

Repeat uploads allocate `<stem>__vNN.<ext>` when a file already exists for the logical key.

Examples (simple):

- Raw: `RAW_DATA/reports/q1.pdf`
- Stage 0: `00_docling_raw/RAW_DATA/reports/q1.pdf.md`
- Stage 1: `01_docling_clean00/RAW_DATA/reports/q1.pdf.md`

Examples (versioned upload):

- Raw: `UPLOAD_DATA/reports/q1__v02.pdf`
- Stage 0: `00_docling_raw/UPLOAD_DATA/reports/q1__v02.pdf.md`

Version compare is numeric (`v2 < v10`). Width is variable (`v1`, `v02`, `v100` are valid).

## Exact-path lookup

API readers resolve **exact** on-disk paths only:

- Concrete paths (`UPLOAD_DATA/...`, `RAW_DATA/...`): file must exist at that key.
- Logical paths: probe `UPLOAD_DATA/<path>` then `RAW_DATA/<path>`.
- No latest-version fallback across sibling `__vNN` files.

Version compare (`v2` < `v10`) applies only to upload allocation, not read resolution.

---

## Model hierarchy

```
OkfFrontmatterStandard          # OKF v0.1 strict (extra=forbid)
        │
        ├── OkfFrontmatterStandardExtra   # read foreign bundles (extra=allow)
        │
        └── ParserOkfFrontmatter          # + ParserOkfExtensionMixin
                  │
                  └── ParserOkfDocument   # frontmatter + body
```

| Model | Module | Purpose |
|-------|--------|---------|
| `OkfFrontmatterStandard` | `okf_standard.py` | OKF v0.1 only |
| `ParserOkfExtensionMixin` | `okf_parser.py` | Project fields |
| `ParserOkfFrontmatter` | `okf_parser.py` | Standard + mixin |
| `ParserOkfDocument` | `okf_parser.py` | Full file |

---

## Mapping to other layers

| Concept | Layer |
|---------|-------|
| OKF v0.1 strict definitions | [OKF_STANDARD_EXTERNAL.md](OKF_STANDARD_EXTERNAL.md) |
| OKF v0.1 upstream spec | [knowledge-catalog/okf/SPEC.md](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) |
| `okf_path` in status | [LAYER_PRESENTATION.md](LAYER_PRESENTATION.md) |
| Versioned path rule | [SPECIFICATION.md](SPECIFICATION.md) |
| BaseModel only | `.cursor/rules/pydantic-basemodel.mdc` |

---

## Changelog

| Date | Change |
|------|--------|
| 2026-07-03 | Real Docling versions in OKF; failure markers; see [DOCLING.md](DOCLING.md) |
| 2026-07-03 | Align with OKF v0.1 standard; `ParserOkfExtensionMixin`; Pydantic YAML validation |
| 2026-07-03 | Initial project-specific OKF prefix |
