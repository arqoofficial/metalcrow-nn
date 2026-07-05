# Step 02 - File Identity and Path Utilities

## Goal

Implement deterministic path/version utilities used by API and workers.

## Prerequisites

- Step 01 accepted (config loader provides `shared_root`).

## Definitions

| Function | Input ? output |
|----------|----------------|
| **Parse logical** | `reports/q1.pdf` ? logical key (no source, no version). |
| **Parse concrete raw** | `RAW_DATA/reports/q1.pdf` or `UPLOAD_DATA/reports/q1__v02.pdf` → source, stem/ext; simple names have no `__vNN`. |
| **Parse concrete OKF** | `01_docling_clean00/UPLOAD_DATA/reports/q1__v02.pdf.md` ? stage folder + embedded concrete raw key. |
| **Next version** | Given existing `__vNN` set for same logical key, return `__v{max+1}` with zero-padded width optional (numeric compare either way). |
| **Exact lookup** | For logical path without source: probe `UPLOAD_DATA/<path>` then `RAW_DATA/<path>`; no latest-version fallback. |
| **Stage mapping** | Raw `UPLOAD_DATA/reports/q1__v02.pdf` ? stage0 `00_docling_raw/UPLOAD_DATA/reports/q1__v02.pdf.md`, stage1 `01_docling_clean00/UPLOAD_DATA/reports/q1__v02.pdf.md`. |
| **Subtree root** | User `root` is relative to implicit `SHARED`; must not start with `SHARED` or `/`; normalize `//`, `.`, `..` per tree contract; reject escape outside `SHARED`. |

## Tasks

1. Replace stub `app/paths.py` with full utilities:
   - parse logical / concrete raw / concrete OKF paths,
   - generate next version filename (`__vNN`),
   - build logical key for version grouping.
2. Implement exact-path resolver in `app/services/path_resolution.py` (`UPLOAD_DATA` then `RAW_DATA` for logical paths).
3. Add helpers:
   - raw ? stage0 OKF path,
   - raw ? stage1 OKF path,
   - normalize/validate subtree roots relative to implicit `SHARED`.
4. Add unit tests for edge cases:
   - malformed versions,
   - missing extensions,
   - multiple sources,
   - non-existing exact paths,
   - `v2` vs `v10` ordering (upload allocation).

## Non-goals

- No queue operations.
- No HTTP endpoint wiring.
- No API integration tests in this step (those belong in step 04 once routers exist).

## Acceptance Criteria

- All path conversions are deterministic and tested.
- Utilities cover both logical and concrete request forms.
- OKF paths preserve full raw filename including `__vNN.<ext>.md`.

## Required Tests (must be implemented and pass)

1. `tests/paths/test_parse.py::test_parse_logical_path`
2. `tests/paths/test_parse.py::test_parse_concrete_raw_path`
3. `tests/paths/test_parse.py::test_parse_concrete_okf_path`
4. `tests/paths/test_versioning.py::test_next_version_from_existing_set`
5. `tests/paths/test_versioning.py::test_numeric_compare_variable_width`
   - `v2 < v10`, `v02` equals numeric `2`.
6. `tests/paths/test_resolution.py::test_logical_path_prefers_upload_over_raw`
7. `tests/paths/test_resolution.py::test_exact_concrete_path_only`
8. `tests/paths/test_mapping.py::test_raw_to_stage0_okf_mapping`
9. `tests/paths/test_mapping.py::test_raw_to_stage1_okf_mapping`
10. `tests/paths/test_tree_root.py::test_subtree_root_normalization`
11. `tests/paths/test_tree_root.py::test_outside_shared_rejected`
12. `tests/paths/test_tree_root.py::test_malformed_version_rejected`

## Verification Command

- `pytest tests/paths -q`

## Integration Tests

None in this step. Path resolution through HTTP is verified in step 04 (`tests/integration/test_path_resolution_api.py`).
