"""Admin panel safety tests."""

from __future__ import annotations

from pathlib import Path

import admin_panel


def test_pid_matches_expected_tokens(monkeypatch) -> None:
    monkeypatch.setattr(
        admin_panel,
        "_pid_cmdline",
        lambda pid: "uv run uvicorn app.main:create_app",
    )
    assert admin_panel._pid_matches(123, ["uvicorn", "app.main"])
    assert not admin_panel._pid_matches(123, ["app.mcp_server"])


def test_stop_refuses_unknown_pid(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(admin_panel, "_read_pid", lambda: 123)
    monkeypatch.setattr(admin_panel, "_remove_pid", lambda: None)
    monkeypatch.setattr(admin_panel, "_read_pid_file", lambda path: None)
    monkeypatch.setattr(admin_panel, "_pid_matches", lambda pid, tokens: False)

    called = {"killed": False}

    def fake_kill(pid: int, sig: int) -> None:
        called["killed"] = True

    monkeypatch.setattr(admin_panel.os, "kill", fake_kill)
    admin_panel.stop()
    assert called["killed"] is False


def test_status_includes_runtime_rows(monkeypatch) -> None:
    def fake_request_json(method: str, path: str) -> tuple[int, dict | str]:
        if path == "/admin/runtime":
            return 200, {
                "queue": {"backend": "memory", "size": 2, "failed_count": 1},
                "chroma": {
                    "ready": True,
                    "collection_name": "advance_rag",
                    "document_count": 42,
                },
                "dense_embedding": {
                    "mode": "cpu_local",
                    "model": "all-MiniLM-L6-v2",
                    "provider": "chromadb_onnx",
                },
            }
        return 0, ""

    monkeypatch.setattr(admin_panel, "_request", lambda method, path: (200, '{"status":"ok"}'))
    monkeypatch.setattr(admin_panel, "_request_json", fake_request_json)
    monkeypatch.setattr(admin_panel, "_read_pid", lambda: None)
    monkeypatch.setattr(admin_panel, "_read_pid_file", lambda path: None)

    rows: list[tuple[str, str, str]] = []

    class FakeTable:
        def add_row(self, probe: str, status: str, body: str) -> None:
            rows.append((probe, status, body))

        def add_column(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(admin_panel, "Table", lambda **_kwargs: FakeTable())
    admin_panel.status()
    assert ("queue/size", "2", "pending index_path jobs") in rows
    assert ("chroma/documents", "42", "collection=advance_rag") in rows
    assert ("dense_embedding/model", "all-MiniLM-L6-v2", "cpu_local via chromadb_onnx") in rows
