# OKF Standard — External Reference (Strict)

Normative definitions from the **Open Knowledge Format (OKF) v0.1 — Draft**, maintained by Google Cloud in the [knowledge-catalog](https://github.com/GoogleCloudPlatform/knowledge-catalog) repository.

| | |
|---|---|
| **Upstream spec** | [okf/SPEC.md](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) |
| **Version** | `0.1` |
| **Retrieved** | 2026-07-03 |
| **Pydantic implementation** | `app/data/okf_standard.py` |
| **Project extensions** | [LAYER_DATA.md](LAYER_DATA.md) (`ParserOkfExtensionMixin`) |

This file records **only** the external standard. Project-specific frontmatter fields (`raw`, `stage`, …) are **not** part of OKF v0.1.

---

## 1. Format identity

OKF is a directory of **UTF-8 markdown files** with **YAML frontmatter**. A self-contained directory tree is a **Knowledge Bundle**.

Distribution forms (any MAY be used):

* Git repository (recommended)
* Tarball or zip archive
* Subdirectory within a larger repository

---

## 2. Terminology (strict)

| Term | Definition |
|------|------------|
| **Knowledge Bundle** | Self-contained hierarchical collection of knowledge documents; unit of distribution. |
| **Concept** | Single unit of knowledge; exactly one markdown document. |
| **Concept ID** | File path within the bundle with `.md` suffix removed. Example: `tables/users.md` → `tables/users`. |
| **Frontmatter** | YAML metadata block at file start, delimited by `---` on its own line. |
| **Body** | All content after the closing frontmatter delimiter. |
| **Link** | Standard markdown link between concepts. |
| **Citation** | Link from a concept to an external source supporting a body claim. |

---

## 3. Reserved filenames

At any directory level, these filenames **MUST NOT** be used for concept documents:

| Filename | Purpose |
|----------|---------|
| `index.md` | Directory listing (progressive disclosure); **no frontmatter** |
| `log.md` | Chronological update history |

All other `.md` files are **concept documents**.

---

## 4. Concept document structure

Every concept document **MUST** consist of exactly two parts:

1. **YAML frontmatter** — opening `---`, YAML mapping, closing `---` (each delimiter on its own line).
2. **Markdown body** — free-form content after frontmatter.

### 4.1 Frontmatter — field definitions

```yaml
---
type: <Type name>                  # REQUIRED
title: <Optional display name>
description: <Optional one-line summary>
resource: <Optional canonical URI for the underlying asset>
tags: [<tag>, <tag>, …]            # Optional
timestamp: <ISO 8601 datetime>     # Optional last-modified time
# … other producer-defined key/value pairs
---
```

#### Required fields

| Field | Type | Constraint | Semantics |
|-------|------|------------|-----------|
| `type` | string | **MUST** be non-empty | Kind of concept. Used for routing, filtering, presentation. Not centrally registered. Consumers **MUST** tolerate unknown values. |

#### Recommended fields (priority order)

| Field | Type | Semantics |
|-------|------|-----------|
| `title` | string | Human-readable display name. If omitted, consumers **MAY** derive from filename. |
| `description` | string | Single-sentence summary. |
| `resource` | URI string | Canonical URI of underlying asset. Omitted for abstract concepts. |
| `tags` | list of strings | Cross-cutting categorization. |
| `timestamp` | ISO 8601 datetime | Last meaningful change. |

#### Extensions

* Producers **MAY** add any additional keys.
* Consumers **SHOULD** preserve unknown keys on round-trip.
* Consumers **SHOULD NOT** reject documents because of unrecognized fields.

#### Strict Pydantic model

`OkfFrontmatterStandard` in `app/data/okf_standard.py` implements **only** the six fields above with `extra="forbid"`.

For reading foreign bundles that include extension keys, use `OkfFrontmatterStandardExtra` (`extra="allow"`).

### 4.2 Body

* Standard markdown.
* Producers **SHOULD** favor structural markdown (headings, lists, tables, fenced code).
* No required body sections.

#### Conventional body headings (SHOULD when applicable)

| Heading | Purpose |
|---------|---------|
| `# Schema` | Structured field/column description |
| `# Examples` | Usage examples |
| `# Citations` | External sources (see §8) |

---

## 5. Cross-linking

Concepts **MAY** link via standard markdown links.

| Form | Syntax | Note |
|------|--------|------|
| Bundle-relative (recommended) | `[label](/path/to/concept.md)` | Leading `/`; stable across moves |
| Relative | `[label](./other.md)` | Standard relative path |

* Link semantics are conveyed by prose, not link type.
* Consumers **MUST** tolerate broken links.

---

## 6. Index files (`index.md`)

* **MAY** appear at any directory level including bundle root.
* **MUST NOT** contain frontmatter.
* Body lists directory contents for progressive disclosure.

---

## 7. Log files (`log.md`)

* **MAY** appear at any hierarchy level.
* Date headings **MUST** use ISO 8601 `YYYY-MM-DD`.
* Newest entries first.

---

## 8. Citations

External sources **SHOULD** be listed under `# Citations` at document bottom, numbered:

```markdown
# Citations

[1] [Source title](https://example.com/...)
```

---

## 9. Conformance (normative)

A bundle is **conformant** with OKF v0.1 if and only if:

1. Every non-reserved `.md` file contains a **parseable YAML frontmatter** block.
2. Every frontmatter block contains a **non-empty `type`** field.
3. Every present reserved file (`index.md`, `log.md`) follows §6 and §7 respectively.

### Permissive consumption

Consumers **SHOULD** treat all other constraints as soft guidance. Consumers **MUST NOT** reject a bundle because of:

* Missing optional frontmatter fields
* Unknown `type` values
* Unknown additional frontmatter keys
* Broken cross-links
* Missing `index.md` files

---

## 10. Versioning

* This standard is OKF **v0.1**.
* Future versions use `<major>.<minor>` semver semantics.
* Bundles **MAY** declare `okf_version: "0.1"` in bundle-root `index.md` frontmatter (only place frontmatter is allowed in `index.md`).

---

## 11. Mapping to code

| OKF v0.1 construct | Python model | Module |
|--------------------|--------------|--------|
| Frontmatter (strict) | `OkfFrontmatterStandard` | `app/data/okf_standard.py` |
| Frontmatter (tolerant reader) | `OkfFrontmatterStandardExtra` | `app/data/okf_standard.py` |
| Concept document | `OkfDocument` | `app/data/okf_standard.py` |
| Parse + validate YAML | `parse_okf_standard()` | `app/data/okf_io.py` |
| Spec version constant | `OKF_SPEC_VERSION` | `app/data/okf_standard.py` |
| Spec URL constant | `OKF_SPEC_URL` | `app/data/okf_standard.py` |

### Validation flow

```text
.md file
  → split_frontmatter()          # §4 structure
  → yaml.safe_load()             # parse YAML
  → OkfFrontmatterStandard.model_validate(data)   # strict field check
  → OkfDocument(frontmatter, body)
```

---

## 12. What this project adds (non-standard)

OKF v0.1 allows producer-defined extension keys. This repository adds pipeline fields via `ParserOkfExtensionMixin` — see [LAYER_DATA.md](LAYER_DATA.md). Those fields are **not** defined in the upstream spec.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-07-03 | Initial strict external reference from knowledge-catalog OKF v0.1 |
