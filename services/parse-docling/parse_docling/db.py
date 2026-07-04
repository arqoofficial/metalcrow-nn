"""Database helpers for the parse-docling Celery worker."""

import psycopg

from parse_docling.config import postgres_dsn


def update_ingest_task(
    task_id: str,
    *,
    status: str,
    stage_name: str,
    progress: float,
    error: str | None = None,
) -> None:
    with psycopg.connect(postgres_dsn()) as conn:
        conn.execute(
            """
            UPDATE experiments.ingest_tasks
            SET status = %(status)s,
                stage_name = %(stage_name)s,
                progress = %(progress)s,
                error = %(error)s,
                updated_at = now()
            WHERE id = %(task_id)s::uuid
            """,
            {
                "status": status,
                "stage_name": stage_name,
                "progress": progress,
                "error": error,
                "task_id": task_id,
            },
        )
        conn.commit()


def get_document(document_id: str) -> dict[str, str | None]:
    with psycopg.connect(postgres_dsn()) as conn:
        row = conn.execute(
            """
            SELECT id::text, parser_path, filename, mime_type
            FROM experiments.documents
            WHERE id = %(document_id)s::uuid
            """,
            {"document_id": document_id},
        ).fetchone()
    if row is None:
        raise ValueError(f"Document {document_id} not found")
    return {
        "id": row[0],
        "parser_path": row[1],
        "filename": row[2],
        "mime_type": row[3],
    }


def set_document_l1(document_id: str, okf_raw_path: str) -> None:
    with psycopg.connect(postgres_dsn()) as conn:
        conn.execute(
            """
            UPDATE experiments.documents
            SET processing_level = 'L1',
                okf_raw_path = %(okf_raw_path)s
            WHERE id = %(document_id)s::uuid
            """,
            {"document_id": document_id, "okf_raw_path": okf_raw_path},
        )
        conn.commit()
