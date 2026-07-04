"""Celery producer — постановка ingest pipeline в очередь (SPEC_V5 §4)."""

import logging
import uuid

from celery import Celery  # type: ignore[import-untyped]
from tool_sdk.queues import build_task_routes

from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery("metalcrow", broker=settings.REDIS_URL)
celery_app.conf.update(
    task_ignore_result=True,
    broker_connection_retry_on_startup=False,
    broker_connection_max_retries=0,
    task_routes=build_task_routes(),
)

V5_PARSE_TASK = "parse.docling.parse"


def _apply_async_safe(signature) -> None:  # noqa: ANN001
    try:
        signature.apply_async()
    except Exception:
        logger.exception("Failed to enqueue Celery task")


def enqueue_l1_parse(task_id: uuid.UUID, document_ids: list[uuid.UUID]) -> None:
    """Phase 0 default path: upload -> parse.docling queue -> OKF raw + L1."""
    args = [str(task_id), [str(d) for d in document_ids]]
    _apply_async_safe(celery_app.signature(V5_PARSE_TASK, args=args))


def enqueue_ingest_pipeline(task_id: uuid.UUID, document_ids: list[uuid.UUID]) -> None:
    """Upload hook: start V5 L1 parse (SPEC_V5 §4)."""
    enqueue_l1_parse(task_id, document_ids)


def enqueue_run(task_id: uuid.UUID, document_ids: list[uuid.UUID], level: str) -> None:
    if level == "L1":
        enqueue_l1_parse(task_id, document_ids)
        return
    raise ValueError(f"Processing level {level} is not implemented yet")
