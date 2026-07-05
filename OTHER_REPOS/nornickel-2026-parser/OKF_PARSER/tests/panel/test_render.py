"""Step 08 - panel layout render tests."""

from datetime import datetime, timezone

from rich.console import Console

from admin_panel.state import PanelState, QueueDepthRow, ServiceRow
from admin_panel.ui.layout import build_layout, render_snapshot, required_sections
from app.presentation.schemas import StatisticsResponse


def test_layout_contains_required_sections() -> None:
    assert required_sections() == ("status", "services", "statistics", "queues", "errors")
    state = PanelState(
        services=[
            ServiceRow(
                component="service/main",
                status="UP",
                details="ok",
                updated_at=datetime.now(timezone.utc),
            )
        ],
        statistics=StatisticsResponse(
            total_raw_files=1,
            stage0_done=0,
            stage1_done=0,
            coverage_ratio=0.0,
        ),
        queue_depths=[
            QueueDepthRow(
                component="service/raw2docling_raw",
                queue_key="parser:jobs:raw2docling_raw",
                depth=4,
            )
        ],
    )
    layout = build_layout(state, api_base_url="http://127.0.0.1:8114", refresh_sec=3)
    for section in ("status", "services", "statistics", "queues", "errors"):
        assert layout[section] is not None


def test_snapshot_renders_queues_widget_with_unavailable_depth() -> None:
    state = PanelState(
        queue_depths=[
            QueueDepthRow(
                component="service/docling_raw2docling_clean00",
                queue_key="parser:jobs:docling_raw2docling_clean00",
                depth=None,
            )
        ]
    )
    snapshot = render_snapshot(state, api_base_url="http://127.0.0.1:8114", refresh_sec=3)
    console = Console(record=True, width=160)
    console.print(snapshot)
    text = console.export_text()
    assert "Queues" in text
    assert "service/docling_raw2docling_" in text
    assert "unavailable" in text
