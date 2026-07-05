"""litsearch → chat integration: read-only poll + action endpoints for the
frontend (Task 12). Everything here is owner-checked through the chain
`LiteraturePaper -> LiteratureSearch -> ChatSession -> User`, mirroring
`chat.py::_get_owned_session` — a search/paper belonging to another user's
session 404s exactly like an unknown id, never leaking existence.
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.models.chat import ChatMessage, ChatRole, ChatSession
from app.models.ingest import IngestStatus, IngestTask
from app.models.litsearch import LiteraturePaper, LiteratureSearch, LitIngestStatus
from app.schemas.litsearch import (
    LitAnswerRef,
    LiteraturePaperPublic,
    LiteratureSearchPublic,
    PaperIngestStatusPublic,
)
from app.services import litsearch as litsearch_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/litsearch", tags=["litsearch"])


def _owned_search(
    session: SessionDep, current_user: CurrentUser, search_id: uuid.UUID
) -> LiteratureSearch:
    search = session.get(LiteratureSearch, search_id)
    if search is None:
        raise HTTPException(status_code=404, detail="Literature search not found")
    chat_session = session.get(ChatSession, search.session_id)
    if chat_session is None or chat_session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Literature search not found")
    return search


def _owned_paper(
    session: SessionDep, current_user: CurrentUser, paper_id: uuid.UUID
) -> LiteraturePaper:
    paper = session.get(LiteraturePaper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Literature paper not found")
    # Reuses `_owned_search` for the rest of the ownership chain — a 404 here
    # (unknown/foreign search) is surfaced as "paper not found" so callers
    # can't distinguish "no such paper" from "paper belongs to someone else".
    try:
        _owned_search(session, current_user, paper.search_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status_code=404, detail="Literature paper not found"
            ) from exc
        raise
    return paper


@router.get("/{search_id}", response_model=LiteratureSearchPublic)
def get_search(
    session: SessionDep, current_user: CurrentUser, search_id: uuid.UUID
) -> LiteratureSearchPublic:
    """GET /api/v1/litsearch/{search_id} — poll target for the frontend while
    a search is in flight, and the source of truth for its papers/answers
    once done."""
    search = _owned_search(session, current_user, search_id)

    # This turn's whole group: the anchor (`search`, always `search.id` here —
    # `followup_of` is only ever written on non-anchor members) plus every
    # search whose `followup_of` points at it, anchor first, members ordered
    # by (created_at, id) — same ordering `agent_continue`/the read tool use.
    member_ids = litsearch_service._turn_search_ids(session, search.id)
    member_searches_by_id = {
        m.id: m
        for m in session.exec(
            select(LiteratureSearch).where(col(LiteratureSearch.id).in_(member_ids[1:]))
        ).all()
    }
    queries = [search.question] + [
        member_searches_by_id[mid].question
        for mid in member_ids[1:]
        if mid in member_searches_by_id
    ]

    # Order by (created_at, id) BEFORE dedup so the kept copy of a duplicate
    # DOI/title is deterministic — the earliest-created (anchor's) row wins,
    # matching agent_continue's union ordering. Without this, Postgres may
    # return the member's copy first and "first occurrence" would flip run to
    # run.
    raw_papers = session.exec(
        select(LiteraturePaper)
        .where(col(LiteraturePaper.search_id).in_(member_ids))
        .order_by(col(LiteraturePaper.created_at), col(LiteraturePaper.id))
    ).all()
    papers = litsearch_service._dedup_papers(raw_papers)

    # Stored `ingest_status` is set once at enqueue time and never updated as the
    # linked `IngestTask` progresses (that's `_coarse_ingest_status`'s job, used by
    # the dedicated `/papers/{id}/ingest-status` poll endpoint) — batch-load the
    # tasks for this search's papers and override each serialized paper's
    # `ingest_status` with the fresh coarse status so this poll target doesn't lie.
    task_ids = {p.ingest_task_id for p in papers if p.ingest_task_id is not None}
    tasks_by_id: dict[uuid.UUID, IngestTask] = {}
    if task_ids:
        tasks = session.exec(
            select(IngestTask).where(col(IngestTask.id).in_(task_ids))
        ).all()
        tasks_by_id = {task.id: task for task in tasks}

    papers_public = []
    for p in papers:
        pub = LiteraturePaperPublic.model_validate(p)
        if p.ingest_task_id is not None:
            task = tasks_by_id.get(p.ingest_task_id)
            if task is not None:
                pub.ingest_status = LitIngestStatus(_coarse_ingest_status(task.status))
        papers_public.append(pub)

    member_id_strs = {str(mid) for mid in member_ids}
    messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == search.session_id)
        .where(ChatMessage.role == ChatRole.ASSISTANT)
        .order_by(col(ChatMessage.created_at))
    ).all()
    answers = [
        LitAnswerRef(message_id=msg.id, kind=msg.message_metadata["litsearch_kind"])
        for msg in messages
        if msg.message_metadata is not None
        and msg.message_metadata.get("search_id") in member_id_strs
    ]

    return LiteratureSearchPublic(
        id=search.id,
        stage=search.stage,
        round=search.round,
        followup_search_id=search.followup_search_id,
        papers=papers_public,
        answers=answers,
        queries=queries,
    )


@router.post(
    "/papers/{paper_id}/add-to-database", response_model=LiteraturePaperPublic
)
def add_paper_to_database(
    session: SessionDep, current_user: CurrentUser, paper_id: uuid.UUID
) -> LiteraturePaperPublic:
    """POST /api/v1/litsearch/papers/{paper_id}/add-to-database — "Добавить в
    базу" chat action (task 10's `add_to_database`, flag-gated ingest)."""
    _owned_paper(session, current_user, paper_id)
    try:
        paper = litsearch_service.add_to_database(session, paper_id)
    except ValueError as exc:
        logger.warning("add_to_database: paper %s not found", paper_id)
        raise HTTPException(status_code=404, detail="Literature paper not found") from exc
    return LiteraturePaperPublic.model_validate(paper)


def _coarse_ingest_status(status: IngestStatus) -> str:
    """Collapse the granular 9-stage `IngestStatus` pipeline vocabulary into
    the coarse `none/queued/running/done/failed` vocabulary the frontend
    polls on (`ingestPollingStatuses` in `LiteraturePanel.tsx` only knows
    `queued`/`running` as non-terminal). Every intermediate processing stage
    (parse/normalize/dedup_link/load/build_flat/embed/sync_neo4j/build_wiki)
    maps to "running" so polling keeps going and the badge stays in the
    generic in-progress state; the detailed stage is still exposed via
    `stage_name` for anyone who wants it."""
    if status == IngestStatus.QUEUED:
        return "queued"
    if status == IngestStatus.DONE:
        return "done"
    if status == IngestStatus.ERROR:
        return "failed"
    return "running"


@router.get(
    "/papers/{paper_id}/ingest-status", response_model=PaperIngestStatusPublic
)
def get_paper_ingest_status(
    session: SessionDep, current_user: CurrentUser, paper_id: uuid.UUID
) -> PaperIngestStatusPublic:
    """GET /api/v1/litsearch/papers/{paper_id}/ingest-status — poll target
    for the "Добавить в базу" action's background ingest pipeline."""
    paper = _owned_paper(session, current_user, paper_id)
    if paper.ingest_task_id is None:
        return PaperIngestStatusPublic()
    task = session.get(IngestTask, paper.ingest_task_id)
    if task is None:
        return PaperIngestStatusPublic()
    return PaperIngestStatusPublic(
        status=_coarse_ingest_status(task.status),
        progress=task.progress,
        stage_name=task.stage_name if task.stage_name is not None else str(task.status),
        error=task.error,
    )
