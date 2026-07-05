from typing import Any

import pytest
from sqlmodel import Session

from app.models.chat import ChatSession
from app.models.litsearch import (
    FetchStatus,
    FulltextStatus,
    LiteraturePaper,
    LiteratureSearch,
    LitStage,
)
from app.services import litsearch as litsearch_service
from app.services import litsearch_client, pdf_text
from tests.utils.storage import FakeStorage
from tests.utils.user import create_random_user


def _make_search(
    db: Session, *, stage: LitStage = LitStage.FETCHING
) -> LiteratureSearch:
    user = create_random_user(db)
    chat_session = ChatSession(user_id=user.id, title="reconcile test")
    db.add(chat_session)
    db.commit()
    db.refresh(chat_session)

    search = LiteratureSearch(session_id=chat_session.id, question="q?", stage=stage)
    db.add(search)
    db.commit()
    db.refresh(search)
    return search


def _make_paper(
    db: Session,
    search: LiteratureSearch,
    *,
    fetch_status: FetchStatus,
    fetch_job_id: str | None = None,
    object_key: str | None = None,
) -> LiteraturePaper:
    paper = LiteraturePaper(
        search_id=search.id,
        doi="10.1/x",
        title="Some Paper",
        authors="A. Author",
        abstract="abstract",
        fetch_status=fetch_status,
        fetch_job_id=fetch_job_id,
        object_key=object_key,
    )
    db.add(paper)
    db.commit()
    db.refresh(paper)
    return paper


def _refresh(db: Session, paper: LiteraturePaper) -> LiteraturePaper:
    db.refresh(paper)
    return paper


def test_reconcile_marks_done_paper_and_failed_paper(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    search = _make_search(db)
    paper_a = _make_paper(
        db, search, fetch_status=FetchStatus.DOWNLOADING, fetch_job_id="jobA"
    )
    paper_b = _make_paper(
        db, search, fetch_status=FetchStatus.DOWNLOADING, fetch_job_id="jobB"
    )

    fake = FakeStorage()
    fake.objects["a.pdf"] = b"%PDF-fake-bytes"
    monkeypatch.setattr(litsearch_service.storage, "open_document", fake.open_document)

    def _fake_job_status(job_id: str) -> dict[str, Any] | None:
        if job_id == "jobA":
            return {"status": "done", "object_key": "a.pdf"}
        if job_id == "jobB":
            return {"status": "failed", "error": "boom"}
        raise AssertionError(f"unexpected job_id {job_id}")

    monkeypatch.setattr(litsearch_client, "job_status", _fake_job_status)
    monkeypatch.setattr(
        pdf_text,
        "extract_text",
        lambda pdf_bytes, *, char_cap: "TXT",  # noqa: ARG005
    )

    result = litsearch_service.reconcile(db, search.id, now_ts=0.0, deadline_ts=999.0)

    assert result is True

    paper_a = _refresh(db, paper_a)
    assert paper_a.fetch_status == FetchStatus.DONE
    assert paper_a.fulltext_status == FulltextStatus.ADDED
    assert paper_a.fulltext_chars == len("TXT")
    assert paper_a.object_key == "a.pdf"

    paper_b = _refresh(db, paper_b)
    assert paper_b.fetch_status == FetchStatus.FAILED
    assert paper_b.fulltext_status == FulltextStatus.FAILED


def test_reconcile_deadline_fails_stuck_downloading_paper(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    search = _make_search(db)
    paper = _make_paper(
        db, search, fetch_status=FetchStatus.DOWNLOADING, fetch_job_id="jobC"
    )

    monkeypatch.setattr(
        litsearch_client,
        "job_status",
        lambda job_id: {"status": "pending"},  # noqa: ARG005
    )

    result = litsearch_service.reconcile(db, search.id, now_ts=1_000.0, deadline_ts=1.0)

    assert result is True
    paper = _refresh(db, paper)
    assert paper.fetch_status == FetchStatus.FAILED
    assert paper.fulltext_status == FulltextStatus.FAILED


def test_reconcile_still_pending_before_deadline_stays_downloading(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    search = _make_search(db)
    paper = _make_paper(
        db, search, fetch_status=FetchStatus.DOWNLOADING, fetch_job_id="jobD"
    )

    monkeypatch.setattr(
        litsearch_client,
        "job_status",
        lambda job_id: {"status": "running"},  # noqa: ARG005
    )

    result = litsearch_service.reconcile(db, search.id, now_ts=0.0, deadline_ts=999.0)

    assert result is False
    paper = _refresh(db, paper)
    assert paper.fetch_status == FetchStatus.DOWNLOADING


def test_reconcile_sweeps_leftover_pending_paper_to_skipped(db: Session) -> None:
    search = _make_search(db)
    paper = _make_paper(db, search, fetch_status=FetchStatus.PENDING)

    result = litsearch_service.reconcile(db, search.id, now_ts=0.0, deadline_ts=999.0)

    assert result is True
    paper = _refresh(db, paper)
    assert paper.fetch_status == FetchStatus.SKIPPED


def test_reconcile_treats_ru_style_skipped_paper_as_terminal_with_no_wait(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RU (Cyberleninka) papers are created directly with
    `fetch_status=SKIPPED`/`fulltext_status=ADDED` (nothing to fetch — the
    full text is already inline from `/search_ru`), never `PENDING` first.
    `reconcile` must see them as terminal on the very first call — an RU-only
    turn's `agent_continue` heartbeat wait must NOT re-enqueue and wait for
    them. Also asserts `job_status` is never called: a SKIPPED paper has no
    `fetch_job_id` to poll, so a call here would signal reconcile is doing
    unnecessary/wrong work."""
    search = _make_search(db)
    paper = LiteraturePaper(
        search_id=search.id,
        doi=None,
        title="Электролитическое рафинирование никеля",
        authors="A. Author",
        abstract="abstract",
        fetch_status=FetchStatus.SKIPPED,
        fulltext_status=FulltextStatus.ADDED,
        fulltext_text="full text from cyberleninka",
        fulltext_chars=len("full text from cyberleninka"),
    )
    db.add(paper)
    db.commit()
    db.refresh(paper)

    def _unexpected_job_status(job_id: str) -> dict[str, Any] | None:
        raise AssertionError(
            f"job_status must not be called for a SKIPPED paper (job_id={job_id})"
        )

    monkeypatch.setattr(litsearch_client, "job_status", _unexpected_job_status)

    result = litsearch_service.reconcile(db, search.id, now_ts=0.0, deadline_ts=999.0)

    assert result is True
    paper = _refresh(db, paper)
    assert paper.fetch_status == FetchStatus.SKIPPED
    assert paper.fulltext_status == FulltextStatus.ADDED


def test_reconcile_wraps_non_pdf_extract_error_as_fulltext_failed(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belt test (T5 carry-forward): even a bare, non-PdfExtractError exception
    from extract_text (some exotic pypdf failure) must be caught — the paper
    still lands DONE (the PDF *was* fetched) but fulltext FAILED, not left
    dangling or propagated to crash the monitor task."""
    search = _make_search(db)
    paper = _make_paper(
        db, search, fetch_status=FetchStatus.DOWNLOADING, fetch_job_id="jobE"
    )

    fake = FakeStorage()
    fake.objects["e.pdf"] = b"%PDF-fake-bytes"
    monkeypatch.setattr(litsearch_service.storage, "open_document", fake.open_document)
    monkeypatch.setattr(
        litsearch_client,
        "job_status",
        lambda job_id: {"status": "done", "object_key": "e.pdf"},  # noqa: ARG005
    )

    def _raise_keyerror(pdf_bytes: bytes, *, char_cap: int) -> str:  # noqa: ARG001
        raise KeyError("exotic pypdf failure")

    monkeypatch.setattr(pdf_text, "extract_text", _raise_keyerror)

    result = litsearch_service.reconcile(db, search.id, now_ts=0.0, deadline_ts=999.0)

    assert result is True
    paper = _refresh(db, paper)
    assert paper.fetch_status == FetchStatus.DONE
    assert paper.fulltext_status == FulltextStatus.FAILED
    assert paper.fulltext_chars == 0


def test_reconcile_wraps_missing_storage_object_as_fulltext_failed(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    search = _make_search(db)
    paper = _make_paper(
        db, search, fetch_status=FetchStatus.DOWNLOADING, fetch_job_id="jobF"
    )

    fake = FakeStorage()  # empty — "f.pdf" was never uploaded
    monkeypatch.setattr(litsearch_service.storage, "open_document", fake.open_document)
    monkeypatch.setattr(
        litsearch_client,
        "job_status",
        lambda job_id: {"status": "done", "object_key": "f.pdf"},  # noqa: ARG005
    )

    result = litsearch_service.reconcile(db, search.id, now_ts=0.0, deadline_ts=999.0)

    assert result is True
    paper = _refresh(db, paper)
    assert paper.fetch_status == FetchStatus.DONE
    assert paper.fulltext_status == FulltextStatus.FAILED
    assert paper.fulltext_chars == 0


def _track_stream_release(fake: FakeStorage) -> tuple[Any, list[str]]:
    """Wraps `fake.open_document` so the returned stream's `close`/
    `release_conn` calls are recorded — used to verify `_mark_fetched`
    releases the MinIO stream back to the pool regardless of outcome."""
    calls: list[str] = []
    original_open_document = fake.open_document

    def _tracking_open_document(*, minio_key: str) -> Any:
        obj = original_open_document(minio_key=minio_key)
        obj.close = lambda: calls.append("close")  # type: ignore[method-assign]
        obj.release_conn = lambda: calls.append("release_conn")  # type: ignore[method-assign]
        return obj

    return _tracking_open_document, calls


def test_reconcile_releases_storage_stream_after_successful_extract(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belt test: the MinIO stream from `storage.open_document` must be
    released (`close`/`release_conn`) once fulltext extraction succeeds, or a
    long-lived worker leaks connections over its lifetime."""
    search = _make_search(db)
    paper = _make_paper(
        db, search, fetch_status=FetchStatus.DOWNLOADING, fetch_job_id="jobH"
    )

    fake = FakeStorage()
    fake.objects["h.pdf"] = b"%PDF-fake-bytes"
    tracking_open_document, calls = _track_stream_release(fake)
    monkeypatch.setattr(litsearch_service.storage, "open_document", tracking_open_document)
    monkeypatch.setattr(
        litsearch_client,
        "job_status",
        lambda job_id: {"status": "done", "object_key": "h.pdf"},  # noqa: ARG005
    )
    monkeypatch.setattr(
        pdf_text,
        "extract_text",
        lambda pdf_bytes, *, char_cap: "TXT",  # noqa: ARG005
    )

    result = litsearch_service.reconcile(db, search.id, now_ts=0.0, deadline_ts=999.0)

    assert result is True
    assert calls == ["close", "release_conn"]
    paper = _refresh(db, paper)
    assert paper.fetch_status == FetchStatus.DONE
    assert paper.fulltext_status == FulltextStatus.ADDED


def test_reconcile_releases_storage_stream_even_when_extract_raises(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same as above but on the failure path (`extract_text` raises) — the
    stream must still be released, not just on the happy path."""
    search = _make_search(db)
    paper = _make_paper(
        db, search, fetch_status=FetchStatus.DOWNLOADING, fetch_job_id="jobI"
    )

    fake = FakeStorage()
    fake.objects["i.pdf"] = b"%PDF-fake-bytes"
    tracking_open_document, calls = _track_stream_release(fake)
    monkeypatch.setattr(litsearch_service.storage, "open_document", tracking_open_document)
    monkeypatch.setattr(
        litsearch_client,
        "job_status",
        lambda job_id: {"status": "done", "object_key": "i.pdf"},  # noqa: ARG005
    )

    def _raise(pdf_bytes: bytes, *, char_cap: int) -> str:  # noqa: ARG001
        raise KeyError("exotic pypdf failure")

    monkeypatch.setattr(pdf_text, "extract_text", _raise)

    result = litsearch_service.reconcile(db, search.id, now_ts=0.0, deadline_ts=999.0)

    assert result is True
    assert calls == ["close", "release_conn"]
    paper = _refresh(db, paper)
    assert paper.fetch_status == FetchStatus.DONE
    assert paper.fulltext_status == FulltextStatus.FAILED


def test_reconcile_mixed_terminal_and_downloading_papers_returns_false(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    search = _make_search(db)
    _make_paper(db, search, fetch_status=FetchStatus.PENDING)
    still_downloading = _make_paper(
        db, search, fetch_status=FetchStatus.DOWNLOADING, fetch_job_id="jobG"
    )

    monkeypatch.setattr(
        litsearch_client,
        "job_status",
        lambda job_id: {"status": "running"},  # noqa: ARG005
    )

    result = litsearch_service.reconcile(db, search.id, now_ts=0.0, deadline_ts=999.0)

    assert result is False
    still_downloading = _refresh(db, still_downloading)
    assert still_downloading.fetch_status == FetchStatus.DOWNLOADING
