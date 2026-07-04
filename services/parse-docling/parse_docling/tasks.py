import logging

from parse_docling import parser_client
from parse_docling.db import get_document, set_document_l1, update_ingest_task
from parse_docling.celery_app import app

logger = logging.getLogger(__name__)


def _document_progress(
    task_id: str,
    *,
    document_index: int,
    document_count: int,
    local_progress: float,
) -> None:
    """Map per-document 0..1 progress into the ingest task for the whole batch."""
    if document_count <= 0:
        return
    span = 1.0 / document_count
    overall = min(document_index * span + local_progress * span, 1.0)
    update_ingest_task(
        task_id,
        status="parse",
        stage_name="parse.docling",
        progress=overall,
    )


@app.task(name="parse.docling.parse")
def parse_document(task_id: str, document_ids: list[str]) -> str:
    """L1: enqueue parser pipeline on existing SHARED raw file -> L1 + okf_raw_path."""
    document_count = len(document_ids)
    update_ingest_task(
        task_id,
        status="parse",
        stage_name="parse.docling",
        progress=0.0,
    )
    try:
        for document_index, document_id in enumerate(document_ids):
            doc = get_document(document_id)
            parser_path = doc["parser_path"] or ""

            def report(local_progress: float) -> None:
                _document_progress(
                    task_id,
                    document_index=document_index,
                    document_count=document_count,
                    local_progress=local_progress,
                )

            report(0.0)
            parser_client.enqueue_process(parser_path)
            report(parser_client._WAIT_PHASE_START)
            okf_path = parser_client.wait_until_done(parser_path, on_progress=report)
            set_document_l1(document_id, okf_path)
            report(1.0)
        update_ingest_task(
            task_id,
            status="done",
            stage_name="parse.docling",
            progress=1.0,
        )
    except Exception as exc:
        logger.exception("parse.docling.parse failed for task %s", task_id)
        update_ingest_task(
            task_id,
            status="error",
            stage_name="parse.docling",
            progress=0.0,
            error=str(exc),
        )
        raise
    return task_id
