"""Celery task for Phase B of the litsearch agent loop (spec §2.4), queue
`litsearch`. `agent_continue` runs the slow full-text tool loop off the web
request; it opens its own short-lived DB session (no request-scoped session in a
worker process, same as the rest of this backend's async pipeline)."""

import logging
import uuid

from sqlmodel import Session

from app.core.db import engine
from app.services import litsearch
from app.services.tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="litsearch.agent_continue")  # type: ignore[untyped-decorator]
def agent_continue(search_id: str, chat_session_id: str) -> None:
    with Session(engine) as session:
        litsearch.agent_continue(
            session, uuid.UUID(search_id), uuid.UUID(chat_session_id)
        )
