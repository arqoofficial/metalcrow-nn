# Admin Panel Specification

Interactive admin panel for operating the parser system from terminal.

Tech stack:

- `typer` for command entrypoints and CLI UX
- `rich` for live panels, tables, colors, and logs

Implements [SPECIFICATION.md](SPECIFICATION.md). Related: [LAYER_PRESENTATION.md](LAYER_PRESENTATION.md), [LAYER_SERVICES.md](LAYER_SERVICES.md), [LAYER_CONFIG.md](LAYER_CONFIG.md).

---

## 1. Goal

Provide an operator-facing terminal panel to:

1. show service health/status,
2. show errors in a clear feed,
3. show statistics from API endpoint(s),
4. trigger basic operational actions.

The panel is for admins/tech leads, not end users.

---

## 2. Scope

### In scope

- Interactive terminal dashboard.
- Auto-refresh of runtime state.
- Error summary and recent error feed.
- Service status board.
- Statistics board powered by API.
- Manual commands: refresh, restart hooks, reindex trigger.

### Out of scope

- Web UI.
- Auth system (inherits service auth policy: none).
- Historical analytics warehouse.
- Deduplication/orphan consistency tooling.

---

## 3. Runtime model

Panel runs as a separate process:

- reads config from `config.yaml` or bundled `config/local.yaml` (see below),
- reads secrets (if needed) from `.env`,
- queries API endpoints (`/health`, `/ready`, `/api/v1/*`),
- optionally checks Redis queue depth and local filesystem state.

It does not host business logic and does not replace services.

### Local development config

When `config.yaml` is absent, `./panel.sh` falls back to `config/local.yaml`:

```yaml
shared_root: ./SHARED
admin_panel:
  api_base_url: http://127.0.0.1:8114
```

Use this when the API runs in Docker and the panel runs on the host.

---

## 4. CLI entrypoints (Typer)

Suggested app command:

- `python -m admin_panel` or `admin-panel`
- `./panel.sh` (project wrapper script, preferred for operators)

Typer commands:

| Command | Purpose |
|--------|---------|
| `admin-panel run` | Start interactive panel |
| `admin-panel once` | Single snapshot (non-live) |
| `admin-panel errors` | Print recent errors only |
| `admin-panel stats` | Print statistics only |
| `admin-panel services` | Print service status only |
| `admin-panel reindex` | Call `/api/v1/reindex` (supports `--yes`) |
| `admin-panel restart` | Run restart hooks script when enabled (supports `--yes`) |

Shell wrapper behavior:

- `./panel.sh` with no arguments runs:
  - `uv run -m admin_panel run --config <config> --env-file ./.env --refresh-sec 3`
- Config resolution order: `$CONFIG_PATH` → `./config.yaml` → `./config/local.yaml`
- `./panel.sh <subcommand> ...` forwards subcommand **and** always passes `--config` / `--env-file`
- `UV`, `CONFIG_PATH`, `ENV_FILE`, `REFRESH_SEC` env vars can override defaults

Suggested options:

- `--config PATH` default `./config.yaml`
- `--env-file PATH` default `./.env`
- `--refresh-sec INT` default `3`
- `--no-color`
- `--json` for machine-readable one-shot outputs

---

## 5. Interactive layout (Rich)

Use `rich.layout.Layout` with three main sections:

1. **Top bar**  
   - environment name  
   - API base URL  
   - current time  
   - refresh interval  
   - last refresh latency

2. **Main body (split)**  
   - left: services status  
   - right: statistics summary

3. **Bottom panel**  
   - recent errors/events feed

Optional footer (interactive `run` mode):

| Key | Action |
|-----|--------|
| `r` | Refresh now |
| `s` | Stats snapshot to event feed |
| `e` | Errors snapshot to event feed |
| `i` | Trigger reindex (confirmation unless disabled) |
| `x` | Run restart hooks (when `allow_restart_hooks: true`) |
| `q` | Quit |

Use `rich.live.Live` for periodic redraw. Key input via non-blocking TTY listener (`admin_panel/keyboard.py`).

---

## 6. Data sources

Primary data source is API:

- `GET /health` — liveness
- `GET /ready` — Redis connectivity via main service
- `GET /api/v1/statistics`

Secondary data source (optional when configured):

- Redis queue depth for stage-0 and stage-1 queues (`admin_panel/refresh.py`)
- lock files for processing hints (`*.worker.lock`, `*.upload.lock`)
- SHARED filesystem accessibility

If one source fails, panel stays up and marks section degraded.

---

## 7. Service status panel

Show at least:

- `service/main` status (`/health` + `/ready`)
- `service/raw2docling_raw` worker pool status (configured count + queue depth)
- `service/docling_raw2docling_clean00` worker pool status (configured count + queue depth)
- Redis reachability
- SHARED filesystem accessibility

Per row fields:

- component
- status (`UP`, `DEGRADED`, `DOWN`, `UNKNOWN`)
- details (short reason)
- updated_at

Status coloring:

- green: `UP`
- yellow: `DEGRADED` / `UNKNOWN`
- red: `DOWN`

---

## 8. Statistics panel

Populate from `GET /api/v1/statistics`.

Statistics include all raw files on disk (simple bootstrap names and upload `__vNN` versions); coverage is per-file stage output.

Display:

- total raw files
- stage coverage counts
- coverage ratio
- last generation timestamp (if API returns it)

If API returns extended stats later, panel may show them in additional rows/columns without breaking base layout.

---

## 9. Errors panel

Error feed combines:

- API call failures (timeouts, non-2xx),
- parse/validation errors inside panel,
- optional service log tail signals (if configured),
- action failures (`reindex`, restart hooks).

Show recent `N` errors (default `50`) ring-buffer style.

Each entry fields:

- timestamp
- severity (`INFO`, `WARN`, `ERROR`)
- source (`api`, `services`, `panel`, `action`)
- message

No persistence required in v1; in-memory buffer is enough.

---

## 10. Actions

Minimum operator actions:

1. `Refresh now`
2. `Trigger reindex` (calls `POST /api/v1/reindex`; optional `enforce` via API body in programmatic use)
3. `Run restart hooks` (optional shell integration, e.g. `rerun.sh` via `admin-panel restart`)

Action requirements:

- explicit confirmation for destructive/expensive actions when `admin_panel.actions.confirm_destructive_actions: true` (bypass with `--yes` on CLI subcommands),
- action result printed to errors/events feed,
- panel must not crash if action fails.

---

## 11. Config contract

Admin panel config lives in `config.yaml` under dedicated section:

```yaml
admin_panel:
  enabled: true
  refresh_sec: 3
  api_base_url: http://127.0.0.1:8114
  error_buffer_size: 50
  request_timeout_sec: 5
  show_lock_files: true
  actions:
    allow_reindex: true
    allow_restart_hooks: false
    restart_hooks_script: rerun.sh
    confirm_destructive_actions: true
```

Secrets (if any) are loaded from `.env`.

Examples:

- `ADMIN_PANEL_API_TOKEN` (optional bearer for future authenticated API)
- `REDIS_URL` (panel reads queue depth directly when set)
- `OTEL_EXPORTER_OTLP_ENDPOINT`, `LANGFUSE_*` (observability; see [LAYER_CONFIG.md](LAYER_CONFIG.md))

---

## 12. Failure behavior

Panel must be resilient:

- degrade per-widget, not whole-process fail,
- retry on next refresh tick,
- show stale age for each panel if updates stop,
- exit codes:
  - `0` on normal quit,
  - non-zero on startup config/validation failure.

---

## 13. Suggested module layout

```text
admin_panel/
  __init__.py
  main.py            # Typer app entrypoint
  config.py          # pydantic config models + loader
  api_client.py      # API wrappers
  state.py           # in-memory state and ring buffers
  ui/
    layout.py        # rich layout composition
    widgets.py       # tables/panels rendering
  actions.py         # reindex/restart commands
  keyboard.py        # non-blocking key listener for interactive mode
  refresh.py         # API + Redis queue depth refresh
```

---

## 14. Acceptance criteria

1. `admin-panel run` starts and refreshes continuously.
2. Services panel shows status for all required components (including queue depth).
3. Statistics panel displays data from `/api/v1/statistics`.
4. Errors panel shows API failures and action failures.
5. Reindex and restart actions work when enabled and report result.
6. Config errors are reported clearly and stop startup.
7. Panel remains alive when one data source is unavailable.
8. `./panel.sh` works against Docker API using `config/local.yaml` when `config.yaml` is absent.

---

## 15. Changelog

| Date | Change |
|------|--------|
| 2026-07-03 | `config/local.yaml` fallback; health/ready probes; queue depth; keyboard shortcuts; restart command |
| 2026-07-03 | Initial admin panel specification (Typer + Rich, services/errors/stats dashboard) |
