"""Typer CLI entrypoint for admin panel."""

from __future__ import annotations

import json
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.live import Live

from admin_panel.actions import trigger_reindex, trigger_restart
from admin_panel.config import load_panel_config
from admin_panel.keyboard import KeyListener, drain_keys
from admin_panel.refresh import refresh_state
from admin_panel.state import PanelState
from admin_panel.ui.layout import build_layout, render_snapshot

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _load(config: Path, env_file: Path):
    try:
        return load_panel_config(config, env_file)
    except Exception as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _handle_key(key: str, cfg, state: PanelState) -> bool:
    lowered = key.lower()
    if lowered == "q":
        return True
    if lowered == "r":
        refresh_state(cfg, state)
        return False
    if lowered == "s":
        from admin_panel.ui.widgets import render_statistics

        console.print(render_statistics(state))
        return False
    if lowered == "e":
        from admin_panel.ui.widgets import render_errors

        console.print(render_errors(state))
        return False
    if lowered == "i":
        console.print(trigger_reindex(cfg, state))
        return False
    if lowered == "x":
        console.print(trigger_restart(cfg, state))
        return False
    return False


@app.command("run")
def run_command(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    env_file: Path = typer.Option(Path(".env"), "--env-file"),
    refresh_sec: int = typer.Option(3, "--refresh-sec"),
    max_ticks: int = typer.Option(0, "--max-ticks", hidden=True),
) -> None:
    """Start interactive Rich dashboard."""
    cfg = _load(config, env_file)
    state = PanelState()
    refresh_state(cfg, state)
    layout = build_layout(state, api_base_url=cfg.resolved_admin_api_base_url, refresh_sec=refresh_sec)

    listener = KeyListener()
    listener.start()
    ticks = 0
    try:
        with Live(layout, console=console, refresh_per_second=4) as live:
            while True:
                deadline = time.time() + refresh_sec
                while time.time() < deadline:
                    if drain_keys(listener, lambda key: _handle_key(key, cfg, state)):
                        raise typer.Exit(code=0)
                    time.sleep(0.1)
                refresh_state(cfg, state)
                live.update(
                    build_layout(
                        state,
                        api_base_url=cfg.resolved_admin_api_base_url,
                        refresh_sec=refresh_sec,
                    )
                )
                ticks += 1
                if max_ticks and ticks >= max_ticks:
                    break
    except KeyboardInterrupt:
        raise typer.Exit(code=0) from None
    finally:
        listener.stop()


@app.command("once")
def once_command(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    env_file: Path = typer.Option(Path(".env"), "--env-file"),
    refresh_sec: int = typer.Option(3, "--refresh-sec"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Print a single snapshot and exit."""
    cfg = _load(config, env_file)
    state = PanelState()
    refresh_state(cfg, state)
    if as_json:
        payload = {
            "services": [row.model_dump(mode="json") for row in state.services],
            "statistics": state.statistics.model_dump(mode="json") if state.statistics else None,
            "errors": [entry.model_dump(mode="json") for entry in state.errors],
        }
        console.print_json(json.dumps(payload))
        return
    console.print(render_snapshot(state, api_base_url=cfg.resolved_admin_api_base_url, refresh_sec=refresh_sec))


@app.command("errors")
def errors_command(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    env_file: Path = typer.Option(Path(".env"), "--env-file"),
) -> None:
    cfg = _load(config, env_file)
    state = PanelState()
    refresh_state(cfg, state)
    from admin_panel.ui.widgets import render_errors

    console.print(render_errors(state))


@app.command("stats")
def stats_command(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    env_file: Path = typer.Option(Path(".env"), "--env-file"),
) -> None:
    cfg = _load(config, env_file)
    state = PanelState()
    refresh_state(cfg, state)
    from admin_panel.ui.widgets import render_statistics

    console.print(render_statistics(state))


@app.command("services")
def services_command(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    env_file: Path = typer.Option(Path(".env"), "--env-file"),
) -> None:
    cfg = _load(config, env_file)
    state = PanelState()
    refresh_state(cfg, state)
    from admin_panel.ui.widgets import render_services

    console.print(render_services(state))


@app.command("reindex")
def reindex_command(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    env_file: Path = typer.Option(Path(".env"), "--env-file"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
) -> None:
    cfg = _load(config, env_file)
    state = PanelState()
    message = trigger_reindex(cfg, state, skip_confirm=yes)
    console.print(message)


@app.command("restart")
def restart_command(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    env_file: Path = typer.Option(Path(".env"), "--env-file"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
) -> None:
    cfg = _load(config, env_file)
    state = PanelState()
    message = trigger_restart(cfg, state, skip_confirm=yes)
    console.print(message)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
