# Step 08 - Admin Panel (Typer + Rich)

## Goal

Implement operator terminal panel per `docs/ADMIN_PANEL.md`.

## Prerequisites

- Steps 01, 04, 07 accepted (config, live API, `reindex.sh` optional reference).
- Step 06 recommended for meaningful services widget data.

## Definitions

| Command | Behavior |
|---------|----------|
| `admin-panel run` | Live Rich dashboard; auto-refresh |
| `admin-panel once` | Single snapshot, exit |
| `admin-panel errors` | Recent errors feed only |
| `admin-panel stats` | `GET /api/v1/statistics` |
| `admin-panel services` | Health: API reachability, optional local process hints |
| `admin-panel reindex` | `POST /api/v1/reindex` |
| `./panel.sh` | No args ? `run`; otherwise pass-through to Typer |

Panel reads `config.yaml` + `.env`; does not host business logic.

## Tasks

1. Create `admin_panel/` package:
   - `main.py`, `config.py`, `api_client.py`, `state.py`, `actions.py`,
   - `ui/layout.py`, `ui/widgets.py`.
2. Implement Typer commands listed above.
3. Implement `panel.sh` at repo root (wrapper).
4. Rich live layout: status line, services, statistics, errors/events feed.
5. Resilience: partial degradation on API/fs failures; panel keeps running.

## Non-goals

- Web UI.
- Auth (inherits none from API).

## Acceptance Criteria

- `./panel.sh` starts panel successfully against running API.
- Reindex action calls API and reports result.
- `stats` matches statistics endpoint fields from step 04.

## Required Tests (must be implemented and pass)

1. `tests/panel/test_cli.py::test_panel_run_command_starts`
2. `tests/panel/test_cli.py::test_panel_once_outputs_snapshot`
3. `tests/panel/test_cli.py::test_panel_errors_command`
4. `tests/panel/test_cli.py::test_panel_stats_command`
5. `tests/panel/test_cli.py::test_panel_services_command`
6. `tests/panel/test_cli.py::test_panel_reindex_command_calls_api`
7. `tests/panel/test_wrapper.py::test_panel_sh_default_invocation`
8. `tests/panel/test_wrapper.py::test_panel_sh_argument_passthrough`
9. `tests/panel/test_resilience.py::test_widget_degradation_does_not_crash_panel`
10. `tests/panel/test_render.py::test_layout_contains_required_sections`

## Verification Command

- `pytest tests/panel -q`

## Integration Tests (must be implemented and pass)

1. `tests/integration/test_panel_runtime.py::test_panel_sh_live_run_boots_with_real_api`
2. `tests/integration/test_panel_runtime.py::test_panel_services_widget_with_running_workers`
3. `tests/integration/test_panel_runtime.py::test_panel_stats_widget_uses_statistics_endpoint`
4. `tests/integration/test_panel_runtime.py::test_panel_reindex_action_hits_reindex_endpoint`
5. `tests/integration/test_panel_runtime.py::test_panel_survives_single_data_source_failure`

## Integration Verification Command

- `pytest tests/integration/test_panel_runtime.py -q`
