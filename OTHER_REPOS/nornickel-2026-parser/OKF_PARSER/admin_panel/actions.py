"""Operational panel actions."""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from app.config.models import AppConfig
from admin_panel.api_client import ApiClient
from admin_panel.state import ErrorSource, PanelState, Severity


def _confirm_action(config: AppConfig, prompt: str) -> bool:
    if not config.admin_panel.actions.confirm_destructive_actions:
        return True
    return typer.confirm(prompt, default=False)


def trigger_reindex(
    config: AppConfig,
    state: PanelState,
    *,
    skip_confirm: bool = False,
) -> str:
    if not config.admin_panel.actions.allow_reindex:
        message = "reindex action disabled in config"
        state.add_error(message, severity=Severity.WARN, source=ErrorSource.action)
        return message

    if not skip_confirm and not _confirm_action(config, "Trigger reindex for all pipeline files?"):
        message = "reindex cancelled"
        state.add_error(message, severity=Severity.INFO, source=ErrorSource.action)
        return message

    client = ApiClient(config)
    try:
        result = client.post_reindex()
        message = f"reindex accepted: enqueued={result.get('enqueued', 0)}"
        state.add_error(message, severity=Severity.INFO, source=ErrorSource.action)
        return message
    except Exception as exc:
        message = f"reindex failed: {exc}"
        state.add_error(message, severity=Severity.ERROR, source=ErrorSource.action)
        return message


def trigger_restart(
    config: AppConfig,
    state: PanelState,
    *,
    skip_confirm: bool = False,
) -> str:
    if not config.admin_panel.actions.allow_restart_hooks:
        message = "restart hooks disabled in config"
        state.add_error(message, severity=Severity.WARN, source=ErrorSource.action)
        return message

    if not skip_confirm and not _confirm_action(config, "Run restart hooks now?"):
        message = "restart cancelled"
        state.add_error(message, severity=Severity.INFO, source=ErrorSource.action)
        return message

    script_name = config.admin_panel.actions.restart_hooks_script
    script_path = Path(script_name)
    if not script_path.is_absolute():
        script_path = Path.cwd() / script_name

    if not script_path.is_file():
        message = f"restart script not found: {script_path}"
        state.add_error(message, severity=Severity.ERROR, source=ErrorSource.action)
        return message

    try:
        result = subprocess.run(
            [str(script_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except Exception as exc:
        message = f"restart failed: {exc}"
        state.add_error(message, severity=Severity.ERROR, source=ErrorSource.action)
        return message

    if result.returncode != 0:
        message = f"restart script failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        state.add_error(message, severity=Severity.ERROR, source=ErrorSource.action)
        return message

    message = f"restart hooks completed: {result.stdout.strip() or script_path.name}"
    state.add_error(message, severity=Severity.INFO, source=ErrorSource.action)
    return message
