"""Rich widgets for admin panel."""

from __future__ import annotations

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from admin_panel.state import PanelState


def render_status_bar(state: PanelState, *, api_base_url: str, refresh_sec: int) -> RenderableType:
    latency = f"{state.last_refresh_ms:.0f}ms" if state.last_refresh_ms is not None else "n/a"
    text = Text.assemble(
        ("API: ", "bold"),
        (api_base_url, "cyan"),
        "  |  ",
        ("Refresh: ", "bold"),
        (f"{refresh_sec}s", "green"),
        "  |  ",
        ("Last: ", "bold"),
        (latency, "yellow"),
        "\n",
        ("Keys: ", "bold"),
        ("r", "green"),
        " refresh  ",
        ("s", "green"),
        " stats  ",
        ("e", "green"),
        " errors  ",
        ("i", "green"),
        " reindex  ",
        ("x", "green"),
        " restart  ",
        ("q", "green"),
        " quit",
    )
    return Panel(text, title="Status", border_style="blue")


def render_services(state: PanelState) -> RenderableType:
    table = Table(title="Services", expand=True)
    table.add_column("Component")
    table.add_column("Status")
    table.add_column("Details")
    for row in state.services:
        style = {"UP": "green", "DEGRADED": "yellow", "DOWN": "red"}.get(row.status, "white")
        table.add_row(row.component, f"[{style}]{row.status}[/{style}]", row.details)
    if not state.services:
        table.add_row("-", "UNKNOWN", "no data")
    return table


def render_statistics(state: PanelState) -> RenderableType:
    table = Table(title="Statistics", expand=True)
    table.add_column("Metric")
    table.add_column("Value")
    stats = state.statistics
    if stats is None:
        table.add_row("status", "unavailable")
        return table
    table.add_row("total_raw_files", str(stats.total_raw_files))
    table.add_row("stage0_done", str(stats.stage0_done))
    table.add_row("stage1_done", str(stats.stage1_done))
    table.add_row("coverage_ratio", f"{stats.coverage_ratio:.2f}")
    return table


def render_queues(state: PanelState) -> RenderableType:
    table = Table(title="Queues", expand=True)
    table.add_column("Component")
    table.add_column("Queue")
    table.add_column("Depth")
    for row in state.queue_depths:
        depth = str(row.depth) if row.depth is not None else "unavailable"
        table.add_row(row.component, row.queue_key, depth)
    if not state.queue_depths:
        table.add_row("-", "-", "unavailable")
    return table


def render_errors(state: PanelState) -> RenderableType:
    table = Table(title="Recent Errors / Events", expand=True)
    table.add_column("Time")
    table.add_column("Severity")
    table.add_column("Source")
    table.add_column("Message")
    for entry in list(state.errors)[:10]:
        table.add_row(
            entry.timestamp.isoformat(timespec="seconds"),
            entry.severity.value,
            entry.source.value,
            entry.message,
        )
    if not state.errors:
        table.add_row("-", "INFO", "panel", "no events")
    return table
