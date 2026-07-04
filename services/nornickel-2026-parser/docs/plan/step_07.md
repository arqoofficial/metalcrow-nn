# Step 07 - Reindex Operations and Server Scripts

## Goal

Operationalize server-side reindex execution.

## Prerequisites

- Step 04 accepted (`POST /api/v1/reindex` implemented).
- Step 03 accepted (`clean_lock.sh` exists — document alongside restart runbooks).

## Definitions

| Script | Purpose |
|--------|---------|
| **`reindex.sh`** | Operator wrapper; calls `POST /api/v1/reindex`; exit `0` on success, non-zero on failure. |
| **`clean_lock.sh`** | Created in step 03; referenced here for full-system restart runbooks. |

`reindex.sh` must read API base URL from config/env (same as admin panel), not hardcode host.

## Tasks

1. Create `reindex.sh` at repo root:
   - POST empty JSON `{}` to reindex endpoint,
   - `curl` (or equivalent) against configured API URL,
   - non-zero exit on HTTP error or connection failure.
2. Parse `config.yaml` + `.env` for `api.host` / `api.port` or `API_BASE_URL`.
3. Print concise progress and result summary (jobs accepted count).
4. Add operator note in `docs/LAYER_INFRASTRUCTURE.md` or `IMPLEMENTATION_NOTES.md` for server runbook usage.

## Non-goals

- No distributed scheduler.
- No changes to reindex API semantics (defined in step 04).

## Acceptance Criteria

- `reindex.sh` works unattended on server.
- Exit codes reliable for automation (`set -e` or explicit status checks).

## Required Tests (must be implemented and pass)

1. `tests/scripts/test_reindex_sh.py::test_reindex_posts_empty_body`
2. `tests/scripts/test_reindex_sh.py::test_reindex_uses_configured_api_url`
3. `tests/scripts/test_reindex_sh.py::test_reindex_nonzero_exit_on_api_failure`
4. `tests/scripts/test_reindex_sh.py::test_reindex_prints_summary`

## Verification Command

- `pytest tests/scripts/test_reindex_sh.py -q`

## Integration Tests (must be implemented and pass)

1. `tests/integration/test_reindex_script.py::test_reindex_script_default`
2. `tests/integration/test_reindex_script.py::test_reindex_script_nonzero_on_api_error`
3. `tests/integration/test_reindex_script.py::test_reindex_script_outputs_summary_for_operators`

## Integration Verification Command

- `pytest tests/integration/test_reindex_script.py -q`
