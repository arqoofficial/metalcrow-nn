# Metalcrow

## Key Gotchas

- uv workspace: the lockfile lives at repo root (`uv.lock`), not per-service/per-app — `uv add`/`uv sync` run from a subdir (e.g. `backend/`) still updates the root lockfile.
- `packages/tool_sdk/tool_sdk/queues.py`'s `queue_for_task()` silently falls back to the task-name prefix when it's missing from `_TASK_QUEUE_MAP` (so a new task queue can appear to "work" without an entry), but `build_task_routes()` (real Celery broker config) requires an explicit map entry — always add new task-name prefixes to `_TASK_QUEUE_MAP` explicitly, don't rely on the fallback.
