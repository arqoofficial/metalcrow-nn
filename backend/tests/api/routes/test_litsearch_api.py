"""Task 12: API router — poll (`GET /{search_id}`), action
(`POST /papers/{id}/add-to-database`), ingest-status poll
(`GET /papers/{id}/ingest-status`). See `.superpowers/sdd/task-12-brief.md`.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models.chat import ChatMessage, ChatRole, ChatSession
from app.models.documents import Document
from app.models.ingest import IngestStatus, IngestTask
from app.models.litsearch import (
    FetchStatus,
    FulltextStatus,
    LiteraturePaper,
    LiteratureSearch,
    LitIngestStatus,
    LitStage,
)
from app.services import litsearch as litsearch_service
from tests.utils.user import create_random_user


def _normal_user_session(db: Session) -> ChatSession:
    """Owns rows under the same user identity `normal_user_token_headers`
    authenticates as (`settings.EMAIL_TEST_USER`, created lazily by
    `authentication_token_from_email` — may not exist yet on first use)."""
    user = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    if user is None:
        from app.models import UserCreate

        user = crud.create_user(
            session=db,
            user_create=UserCreate(
                email=settings.EMAIL_TEST_USER, password="testpassword123"
            ),
        )
    chat_session = ChatSession(user_id=user.id, title="litsearch api test")
    db.add(chat_session)
    db.commit()
    db.refresh(chat_session)
    return chat_session


def _make_search(
    db: Session, chat_session: ChatSession, *, followup_search_id: uuid.UUID | None = None
) -> LiteratureSearch:
    search = LiteratureSearch(
        session_id=chat_session.id,
        question="What is the yield strength of Ti-6Al-4V?",
        stage=LitStage.DONE,
        round=1,
        followup_search_id=followup_search_id,
    )
    db.add(search)
    db.commit()
    db.refresh(search)
    return search


def _make_paper(db: Session, search: LiteratureSearch, **overrides: Any) -> LiteraturePaper:
    defaults: dict[str, Any] = {
        "search_id": search.id,
        "doi": "10.1/abc",
        "title": "A Paper",
        "authors": "A. Author",
        "year": 2020,
        "abstract": "abstract text",
        "pdf_url": "http://example.com/a.pdf",
        "citation_count": 3,
        "fetch_status": FetchStatus.DONE,
        "fulltext_status": FulltextStatus.ADDED,
        "fulltext_chars": 1234,
        "ingest_status": LitIngestStatus.NONE,
    }
    defaults.update(overrides)
    paper = LiteraturePaper(**defaults)
    db.add(paper)
    db.commit()
    db.refresh(paper)
    return paper


def _make_answer_message(
    db: Session, chat_session: ChatSession, search: LiteratureSearch, kind: str
) -> ChatMessage:
    message = ChatMessage(
        session_id=chat_session.id,
        role=ChatRole.ASSISTANT,
        content="Here is what the literature says.",
        message_metadata={"search_id": str(search.id), "litsearch_kind": kind},
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def test_litsearch_requires_auth(client: TestClient) -> None:
    search_id = uuid.uuid4()
    paper_id = uuid.uuid4()
    assert client.get(f"{settings.API_V1_STR}/litsearch/{search_id}").status_code == 401
    assert (
        client.post(
            f"{settings.API_V1_STR}/litsearch/papers/{paper_id}/add-to-database"
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"{settings.API_V1_STR}/litsearch/papers/{paper_id}/ingest-status"
        ).status_code
        == 401
    )


def test_get_search_happy_path(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    followup_id = uuid.uuid4()
    chat_session = _normal_user_session(db)
    search = _make_search(db, chat_session, followup_search_id=followup_id)
    paper = _make_paper(db, search)
    answer = _make_answer_message(db, chat_session, search, kind="abstracts")
    # A user-role message and an assistant message for a *different* search
    # must not leak into `answers`.
    db.add(
        ChatMessage(
            session_id=chat_session.id, role=ChatRole.USER, content="a question"
        )
    )
    db.add(
        ChatMessage(
            session_id=chat_session.id,
            role=ChatRole.ASSISTANT,
            content="unrelated",
            message_metadata={"search_id": str(uuid.uuid4()), "litsearch_kind": "fulltext"},
        )
    )
    db.commit()

    r = client.get(
        f"{settings.API_V1_STR}/litsearch/{search.id}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(search.id)
    assert body["stage"] == "done"
    assert body["round"] == 1
    assert body["followup_search_id"] == str(followup_id)
    assert len(body["papers"]) == 1
    assert body["papers"][0]["id"] == str(paper.id)
    assert body["papers"][0]["title"] == "A Paper"
    assert len(body["answers"]) == 1
    assert body["answers"][0] == {"message_id": str(answer.id), "kind": "abstracts"}


def test_get_search_returns_turn_union_and_all_queries(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    """The panel route (`GET /litsearch/{anchor}`) must aggregate the WHOLE
    turn, not just the anchor's own row: papers from every `followup_of`
    member (deduped by DOI), every member's question (anchor first), and
    answers tagged with any member's id."""
    chat_session = _normal_user_session(db)
    anchor = _make_search(db, chat_session)
    anchor.question = "What is the yield strength of Ti-6Al-4V?"
    db.add(anchor)
    db.commit()
    db.refresh(anchor)

    member = LiteratureSearch(
        session_id=chat_session.id,
        question="What about its fatigue limit?",
        stage=LitStage.DONE,
        round=1,
        followup_of=anchor.id,
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    # Same DOI on both searches — must be deduped to a single paper in the
    # union (first occurrence, i.e. the anchor's copy, kept).
    anchor_paper = _make_paper(db, anchor, doi="10.1/shared", title="Anchor paper")
    _make_paper(db, member, doi="10.1/shared", title="Duplicate of anchor paper")
    member_only_paper = _make_paper(
        db, member, doi="10.1/member-only", title="Member-only paper"
    )

    anchor_answer = _make_answer_message(db, chat_session, anchor, kind="abstracts")
    member_answer = _make_answer_message(db, chat_session, member, kind="fulltext")

    r = client.get(
        f"{settings.API_V1_STR}/litsearch/{anchor.id}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 200
    body = r.json()

    assert body["id"] == str(anchor.id)
    assert body["queries"] == [
        "What is the yield strength of Ti-6Al-4V?",
        "What about its fatigue limit?",
    ]

    paper_ids = {p["id"] for p in body["papers"]}
    assert paper_ids == {str(anchor_paper.id), str(member_only_paper.id)}
    assert len(body["papers"]) == 2  # the duplicate DOI is deduped out

    answer_message_ids = {a["message_id"] for a in body["answers"]}
    assert answer_message_ids == {str(anchor_answer.id), str(member_answer.id)}


def test_get_search_reflects_fresh_ingest_task_status(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    """A paper's STORED `ingest_status` (set once at enqueue time) goes stale
    once the linked `IngestTask` progresses — `GET /litsearch/{id}` must
    report the fresh coarse status derived from the `IngestTask`, exactly
    like the dedicated `GET /papers/{id}/ingest-status` endpoint does, not
    the stale stored value ("queued" here even though the task is DONE)."""
    chat_session = _normal_user_session(db)
    search = _make_search(db, chat_session)
    task = IngestTask(status=IngestStatus.DONE, progress=1.0, stage_name="build_wiki", error=None)
    db.add(task)
    db.commit()
    db.refresh(task)
    paper = _make_paper(
        db, search, ingest_status=LitIngestStatus.QUEUED, ingest_task_id=task.id
    )

    r = client.get(
        f"{settings.API_V1_STR}/litsearch/{search.id}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["papers"]) == 1
    assert body["papers"][0]["id"] == str(paper.id)
    assert body["papers"][0]["ingest_status"] == "done"


def test_get_search_paper_without_ingest_task_keeps_stored_status(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    """A paper with no `ingest_task_id` has nothing to derive a fresh status
    from — it should keep its own stored value (default "none") and not
    crash."""
    chat_session = _normal_user_session(db)
    search = _make_search(db, chat_session)
    paper = _make_paper(db, search)
    assert paper.ingest_task_id is None

    r = client.get(
        f"{settings.API_V1_STR}/litsearch/{search.id}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["papers"]) == 1
    assert body["papers"][0]["ingest_status"] == "none"


def test_get_search_ingest_status_matches_dedicated_endpoint(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    """Regression: the `ingest_status` embedded in `GET /litsearch/{id}` must
    agree with `GET /papers/{id}/ingest-status` for the same paper — same
    coarse-status derivation, two call sites."""
    chat_session = _normal_user_session(db)
    search = _make_search(db, chat_session)
    task = IngestTask(status=IngestStatus.EMBED, progress=0.6, stage_name="embed", error=None)
    db.add(task)
    db.commit()
    db.refresh(task)
    paper = _make_paper(
        db, search, ingest_status=LitIngestStatus.RUNNING, ingest_task_id=task.id
    )

    search_body = client.get(
        f"{settings.API_V1_STR}/litsearch/{search.id}",
        headers=normal_user_token_headers,
    ).json()
    status_body = client.get(
        f"{settings.API_V1_STR}/litsearch/papers/{paper.id}/ingest-status",
        headers=normal_user_token_headers,
    ).json()

    assert search_body["papers"][0]["ingest_status"] == status_body["status"]
    assert search_body["papers"][0]["ingest_status"] == "running"


def test_get_search_not_found(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.get(
        f"{settings.API_V1_STR}/litsearch/{uuid.uuid4()}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 404


def test_get_search_not_owned(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    other_user = create_random_user(db)
    other_session = ChatSession(user_id=other_user.id, title="other user's session")
    db.add(other_session)
    db.commit()
    db.refresh(other_session)
    search = _make_search(db, other_session)

    r = client.get(
        f"{settings.API_V1_STR}/litsearch/{search.id}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 404


def test_add_to_database_happy_path(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_session = _normal_user_session(db)
    search = _make_search(db, chat_session)
    paper = _make_paper(db, search)

    def _fake_add_to_database(session: Session, paper_id: uuid.UUID) -> LiteraturePaper:
        # `session` here is the request-scoped `SessionDep`, distinct from
        # the module-scoped `db` fixture session `paper` was created in —
        # re-fetch through `session` rather than reusing/mutating `paper`
        # directly (mixing sessions on one ORM instance raises).
        assert paper_id == paper.id
        document = Document(minio_key="k.pdf", filename="k.pdf")
        session.add(document)
        session.commit()
        session.refresh(document)
        request_scoped_paper = session.get(LiteraturePaper, paper_id)
        assert request_scoped_paper is not None
        request_scoped_paper.document_id = document.id
        session.add(request_scoped_paper)
        session.commit()
        session.refresh(request_scoped_paper)
        return request_scoped_paper

    monkeypatch.setattr(litsearch_service, "add_to_database", _fake_add_to_database)

    r = client.post(
        f"{settings.API_V1_STR}/litsearch/papers/{paper.id}/add-to-database",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(paper.id)
    # `paper` (built through the `db` fixture session) is a distinct ORM
    # instance from the one the request handled and committed through — its
    # in-memory `document_id` never gets refreshed by that commit, so assert
    # against the response body itself, not `paper.document_id`.
    assert body["document_id"] is not None


def test_add_to_database_unknown_paper(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/litsearch/papers/{uuid.uuid4()}/add-to-database",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 404


def test_add_to_database_not_owned(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    other_user = create_random_user(db)
    other_session = ChatSession(user_id=other_user.id, title="other user's session")
    db.add(other_session)
    db.commit()
    db.refresh(other_session)
    search = _make_search(db, other_session)
    paper = _make_paper(db, search)

    r = client.post(
        f"{settings.API_V1_STR}/litsearch/papers/{paper.id}/add-to-database",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 404


def test_ingest_status_none(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    chat_session = _normal_user_session(db)
    search = _make_search(db, chat_session)
    paper = _make_paper(db, search)
    assert paper.ingest_task_id is None

    r = client.get(
        f"{settings.API_V1_STR}/litsearch/papers/{paper.id}/ingest-status",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 200
    assert r.json() == {
        "status": "none",
        "progress": 0.0,
        "stage_name": None,
        "error": None,
    }


def test_ingest_status_maps_task(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    """An intermediate pipeline stage (EMBED) must collapse to the coarse
    "running" the frontend polls on, not leak as the raw "embed" string —
    see `_coarse_ingest_status` in `app.api.routes.litsearch`. The granular
    stage is still surfaced via `stage_name`."""
    chat_session = _normal_user_session(db)
    search = _make_search(db, chat_session)
    task = IngestTask(
        status=IngestStatus.EMBED,
        progress=0.6,
        stage_name="embed",
        error=None,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    paper = _make_paper(
        db, search, ingest_status=LitIngestStatus.RUNNING, ingest_task_id=task.id
    )

    r = client.get(
        f"{settings.API_V1_STR}/litsearch/papers/{paper.id}/ingest-status",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "running"
    assert body["progress"] == 0.6
    assert body["stage_name"] == "embed"
    assert body["error"] is None


@pytest.mark.parametrize(
    ("raw_status", "expected_coarse"),
    [
        (IngestStatus.QUEUED, "queued"),
        (IngestStatus.DONE, "done"),
        (IngestStatus.ERROR, "failed"),
        (IngestStatus.PARSE, "running"),
        (IngestStatus.NORMALIZE, "running"),
        (IngestStatus.DEDUP_LINK, "running"),
        (IngestStatus.LOAD, "running"),
        (IngestStatus.BUILD_FLAT, "running"),
        (IngestStatus.SYNC_NEO4J, "running"),
        (IngestStatus.BUILD_WIKI, "running"),
    ],
)
def test_ingest_status_coarse_mapping(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
    raw_status: IngestStatus,
    expected_coarse: str,
) -> None:
    """Every granular `IngestStatus` value maps to the coarse
    none/queued/running/done/failed vocabulary the frontend understands
    (`ingestPollingStatuses` / `IngestStatusBadge` in `LiteraturePanel.tsx`)."""
    chat_session = _normal_user_session(db)
    search = _make_search(db, chat_session)
    task = IngestTask(status=raw_status, progress=0.5, stage_name=None, error=None)
    db.add(task)
    db.commit()
    db.refresh(task)
    paper = _make_paper(
        db, search, ingest_status=LitIngestStatus.RUNNING, ingest_task_id=task.id
    )

    r = client.get(
        f"{settings.API_V1_STR}/litsearch/papers/{paper.id}/ingest-status",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == expected_coarse
    # stage_name falls back to the raw granular status when the task didn't
    # set one explicitly, so the UI can still show it if it wants to.
    assert body["stage_name"] == str(raw_status)


def test_ingest_status_not_owned(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    other_user = create_random_user(db)
    other_session = ChatSession(user_id=other_user.id, title="other user's session")
    db.add(other_session)
    db.commit()
    db.refresh(other_session)
    search = _make_search(db, other_session)
    paper = _make_paper(db, search)

    r = client.get(
        f"{settings.API_V1_STR}/litsearch/papers/{paper.id}/ingest-status",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 404
