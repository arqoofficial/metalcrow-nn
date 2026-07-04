"""Tests for parse.docling L1 orchestration."""

from unittest.mock import MagicMock

from parse_docling import parser_client
from parse_docling.tasks import parse_document


def test_parse_document_enqueues_and_sets_l1(monkeypatch) -> None:  # noqa: ANN001
    from parse_docling import tasks as tasks_module

    monkeypatch.setattr(
        tasks_module,
        "get_document",
        lambda document_id: {
            "id": document_id,
            "parser_path": "UPLOAD_DATA/metalcrow/doc/report.pdf",
            "filename": "report.pdf",
            "mime_type": "application/pdf",
        },
    )
    updates: list[dict] = []
    monkeypatch.setattr(
        tasks_module,
        "update_ingest_task",
        lambda task_id, **kwargs: updates.append({"task_id": task_id, **kwargs}),
    )
    set_l1_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        tasks_module,
        "set_document_l1",
        lambda document_id, okf_path: set_l1_calls.append((document_id, okf_path)),
    )
    monkeypatch.setattr(parser_client, "enqueue_process", MagicMock())
    def fake_wait(resolved_path: str, *, on_progress=None) -> str:  # noqa: ANN001
        if on_progress is not None:
            on_progress(1.0)
        return "01_docling_clean00/UPLOAD_DATA/metalcrow/doc/report.pdf.md"

    monkeypatch.setattr(parser_client, "wait_until_done", fake_wait)

    parse_document.run("task-1", ["doc-1"])

    parser_client.enqueue_process.assert_called_once_with(
        "UPLOAD_DATA/metalcrow/doc/report.pdf"
    )
    assert set_l1_calls == [
        ("doc-1", "01_docling_clean00/UPLOAD_DATA/metalcrow/doc/report.pdf.md")
    ]
    assert updates[-1]["status"] == "done"
