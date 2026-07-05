"""Task 10: `add_to_database` — flag-gated ingest, idempotent ("Добавить в
базу" action). See `.superpowers/sdd/task-10-brief.md`."""

import uuid
from typing import Any

import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.models.chat import ChatSession
from app.models.documents import Document
from app.models.ingest import IngestTask
from app.models.litsearch import (
    FetchStatus,
    LiteraturePaper,
    LiteratureSearch,
    LitIngestStatus,
    LitStage,
)
from app.services import litsearch as litsearch_service
from app.services import tasks
from tests.utils.user import create_random_user


def _make_search(db: Session) -> LiteratureSearch:
    user = create_random_user(db)
    chat_session = ChatSession(user_id=user.id, title="add_to_database test")
    db.add(chat_session)
    db.commit()
    db.refresh(chat_session)

    search = LiteratureSearch(
        session_id=chat_session.id, question="q?", stage=LitStage.DONE
    )
    db.add(search)
    db.commit()
    db.refresh(search)
    return search


def _make_paper(
    db: Session,
    search: LiteratureSearch,
    *,
    object_key: str | None = "existing.pdf",
    doi: str | None = "10.1/x",
    pdf_url: str | None = "http://example.com/x.pdf",
) -> LiteraturePaper:
    paper = LiteraturePaper(
        search_id=search.id,
        doi=doi,
        title="Some Paper",
        authors="A. Author",
        abstract="abstract",
        pdf_url=pdf_url,
        fetch_status=FetchStatus.DONE,
        object_key=object_key,
    )
    db.add(paper)
    db.commit()
    db.refresh(paper)
    return paper


def _count_documents(db: Session) -> int:
    return len(db.exec(select(Document)).all())


def _count_ingest_tasks(db: Session) -> int:
    return len(db.exec(select(IngestTask)).all())


def _patch_enqueue_counter(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    calls: list[tuple] = []

    def _fake_enqueue_l1_parse(
        task_id: uuid.UUID, document_ids: list[uuid.UUID]
    ) -> None:
        calls.append((task_id, document_ids))

    monkeypatch.setattr(tasks, "enqueue_l1_parse", _fake_enqueue_l1_parse)
    return calls


def test_add_to_database_flag_off_stages_l0_only(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LITSEARCH_INGEST_ENABLED", False)
    calls = _patch_enqueue_counter(monkeypatch)

    search = _make_search(db)
    paper = _make_paper(db, search, object_key="k.pdf")
    ingest_tasks_before = _count_ingest_tasks(db)

    result = litsearch_service.add_to_database(db, paper.id)

    assert result.document_id is not None
    document = db.get(Document, result.document_id)
    assert document is not None
    assert document.minio_key == "k.pdf"
    assert result.ingest_status == LitIngestStatus.NONE
    assert result.ingest_task_id is None
    assert calls == []
    assert _count_ingest_tasks(db) == ingest_tasks_before


def test_add_to_database_flag_on_enqueues_ingest(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LITSEARCH_INGEST_ENABLED", True)
    calls = _patch_enqueue_counter(monkeypatch)

    search = _make_search(db)
    paper = _make_paper(db, search, object_key="k2.pdf")

    result = litsearch_service.add_to_database(db, paper.id)

    assert result.document_id is not None
    assert result.ingest_status == LitIngestStatus.QUEUED
    assert result.ingest_task_id is not None
    task = db.get(IngestTask, result.ingest_task_id)
    assert task is not None
    assert task.document_ids == [str(result.document_id)]
    assert len(calls) == 1
    called_task_id, called_document_ids = calls[0]
    assert called_task_id == task.id
    assert called_document_ids == [result.document_id]


def test_add_to_database_idempotent(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LITSEARCH_INGEST_ENABLED", False)
    _patch_enqueue_counter(monkeypatch)

    search = _make_search(db)
    paper = _make_paper(db, search, object_key="k3.pdf")

    first = litsearch_service.add_to_database(db, paper.id)
    docs_after_first = _count_documents(db)

    second = litsearch_service.add_to_database(db, paper.id)
    docs_after_second = _count_documents(db)

    assert second.document_id == first.document_id
    assert docs_after_second == docs_after_first


def test_add_to_database_missing_object_key_fetches_sync(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LITSEARCH_INGEST_ENABLED", False)
    _patch_enqueue_counter(monkeypatch)

    calls: list[tuple[str, str | None]] = []

    def _fake_fetch_sync(doi: str, *, url: str | None) -> dict[str, Any] | None:
        calls.append((doi, url))
        return {"doi": doi, "object_key": "fetched.pdf", "url": url}

    monkeypatch.setattr(
        litsearch_service.litsearch_client, "fetch_sync", _fake_fetch_sync
    )

    search = _make_search(db)
    paper = _make_paper(
        db, search, object_key=None, doi="10.1/y", pdf_url="http://x/y.pdf"
    )

    result = litsearch_service.add_to_database(db, paper.id)

    assert calls == [("10.1/y", "http://x/y.pdf")]
    assert result.object_key == "fetched.pdf"
    assert result.document_id is not None
    document = db.get(Document, result.document_id)
    assert document is not None
    assert document.minio_key == "fetched.pdf"


def test_add_to_database_fetch_sync_fails_marks_failed(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LITSEARCH_INGEST_ENABLED", False)
    _patch_enqueue_counter(monkeypatch)

    def _fake_fetch_sync(doi: str, *, url: str | None) -> dict[str, Any] | None:
        del doi, url
        return None

    monkeypatch.setattr(
        litsearch_service.litsearch_client, "fetch_sync", _fake_fetch_sync
    )

    search = _make_search(db)
    paper = _make_paper(db, search, object_key=None, doi="10.1/z", pdf_url=None)
    docs_before = _count_documents(db)

    result = litsearch_service.add_to_database(db, paper.id)

    assert result.document_id is None
    assert result.ingest_status == LitIngestStatus.FAILED
    assert _count_documents(db) == docs_before


def test_add_to_database_missing_paper_raises(db: Session) -> None:
    with pytest.raises(ValueError):
        litsearch_service.add_to_database(db, uuid.uuid4())


def test_add_to_database_lost_claim_race_no_orphan(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates losing the guarded-claim race: another concurrent
    `add_to_database` call commits its own `UPDATE ... document_id=...`
    between our idempotency check and our own claim `UPDATE`, so ours
    matches 0 rows. `session.exec` is the only thing `add_to_database` calls
    (besides `session.get`) to run that claim `UPDATE` — monkeypatching it to
    hand back a `rowcount == 0` result deterministically exercises the
    lost-race branch without needing a second real DB connection. Asserts
    the loser leaves no orphaned `Document`, doesn't touch `document_id`, and
    never enqueues."""
    monkeypatch.setattr(settings, "LITSEARCH_INGEST_ENABLED", True)
    calls = _patch_enqueue_counter(monkeypatch)

    search = _make_search(db)
    paper = _make_paper(db, search, object_key="race.pdf")
    docs_before = _count_documents(db)
    tasks_before = _count_ingest_tasks(db)

    class _LostRaceResult:
        rowcount = 0

    def _fake_exec(statement: Any, *args: Any, **kwargs: Any) -> Any:
        del statement, args, kwargs
        # `add_to_database` only calls `session.exec` once — for the
        # guarded claim `UPDATE`. Faking its rowcount to 0 simulates
        # another concurrent caller having already won that claim.
        return _LostRaceResult()

    original_exec = db.exec
    monkeypatch.setattr(db, "exec", _fake_exec)

    result = litsearch_service.add_to_database(db, paper.id)

    # Restore the real `exec` before using the session for post-call
    # assertions (the fake stub has no `.all()`/real result behaviour).
    monkeypatch.setattr(db, "exec", original_exec)

    assert result.document_id is None
    assert result.id == paper.id
    assert _count_documents(db) == docs_before
    assert _count_ingest_tasks(db) == tasks_before
    assert calls == []


def test_provenance_seam_flag_defaults_off_and_helper_surfaces_doi(
    db: Session,
) -> None:
    """Provenance seam (task 10 follow-up, OSN-scoped): the
    `_ATTACH_PROVENANCE_TO_DOCUMENT` flag must default off — attaching
    provenance to the shared `experiments.documents` model needs new columns
    that are pending cross-team sign-off (see the constant's comment in
    `litsearch.py`) — and `_paper_provenance` must already surface the
    paper's DOI, ready for that future flip-on.
    """
    assert litsearch_service._ATTACH_PROVENANCE_TO_DOCUMENT is False

    search = _make_search(db)
    paper = _make_paper(db, search, doi="10.5555/provenance-test")

    provenance = litsearch_service._paper_provenance(paper)

    assert provenance["doi"] == "10.5555/provenance-test"
