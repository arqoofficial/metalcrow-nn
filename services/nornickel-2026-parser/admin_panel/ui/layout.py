"""Rich layout composition for admin panel."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.layout import Layout

from admin_panel.state import PanelState
from admin_panel.ui import widgets


def build_layout(state: PanelState, *, api_base_url: str, refresh_sec: int) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="status", size=5),
        Layout(name="body"),
        Layout(name="errors", size=10),
    )
    layout["body"].split_row(
        Layout(name="services"),
        Layout(name="statistics"),
        Layout(name="queues"),
    )
    layout["status"].update(widgets.render_status_bar(state, api_base_url=api_base_url, refresh_sec=refresh_sec))
    layout["services"].update(widgets.render_services(state))
    layout["statistics"].update(widgets.render_statistics(state))
    layout["queues"].update(widgets.render_queues(state))
    layout["errors"].update(widgets.render_errors(state))
    return layout


def render_snapshot(state: PanelState, *, api_base_url: str, refresh_sec: int) -> RenderableType:
    return Group(
        widgets.render_status_bar(state, api_base_url=api_base_url, refresh_sec=refresh_sec),
        widgets.render_services(state),
        widgets.render_statistics(state),
        widgets.render_queues(state),
        widgets.render_errors(state),
    )


def required_sections() -> tuple[str, ...]:
    return ("status", "services", "statistics", "queues", "errors")
