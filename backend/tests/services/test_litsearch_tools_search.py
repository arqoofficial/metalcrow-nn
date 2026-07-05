import uuid
from typing import Any

import pytest
from sqlmodel import Session, select

from app.models.chat import ChatSession
from app.models.litsearch import (
    FetchStatus,
    FulltextStatus,
    LiteraturePaper,
    LiteratureSearch,
    LitStage,
)
from app.services import litsearch_client, litsearch_tools
from tests.utils.user import create_random_user

_PAPERS: list[dict[str, Any]] = [
    {"doi": "10.1/a", "title": "Paper A", "authors": "A", "year": 2020,
     "abstract": "abs a", "pdf_url": "http://x/a.pdf", "citation_count": 3},
    {"doi": None, "title": "Paper B", "authors": "B", "year": 2021,
     "abstract": "abs b", "pdf_url": None, "citation_count": None},
]

_RU_PAPERS: list[dict[str, Any]] = [
    {
        "doi": None,
        "title": "Извлечение никеля",
        "authors": "А. Б.",
        "year": 2019,
        "abstract": "аннотация",
        "fulltext": "полный текст статьи про никель",
        "pdf_url": None,
        "citation_count": None,
        "source": "cyberleninka",
        "url": "https://cyberleninka.ru/article/n/x",
    },
    {
        "doi": None,
        "title": "Флотация руды",
        "authors": "В. Г.",
        "year": 2021,
        "abstract": "аннотация 2",
        "fulltext": "",  # simulates a page fetch that came back empty
        "pdf_url": None,
        "citation_count": None,
        "source": "cyberleninka",
        "url": "https://cyberleninka.ru/article/n/y",
    },
]


def _chat_session(db: Session) -> ChatSession:
    user = create_random_user(db)
    cs = ChatSession(user_id=user.id, title="tools search test")
    db.add(cs)
    db.commit()
    db.refresh(cs)
    return cs


def test_litsearch_search_persists_rows_fires_fetch_and_returns_payload(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = _chat_session(db)
    monkeypatch.setattr(litsearch_client, "search", lambda q, n: _PAPERS)  # noqa: ARG005
    monkeypatch.setattr(
        litsearch_client, "fetch_async",
        lambda doi, *, url, conversation_id: "job1",  # noqa: ARG005
    )

    result = litsearch_tools.litsearch_search(db, cs.id, query="nickel")

    search = db.exec(
        select(LiteratureSearch).where(LiteratureSearch.session_id == cs.id)
    ).one()
    assert result["search_id"] == str(search.id)
    assert search.stage == LitStage.FETCHING

    papers = db.exec(
        select(LiteraturePaper).where(LiteraturePaper.search_id == search.id)
    ).all()
    assert len(papers) == 2
    with_doi = next(p for p in papers if p.doi == "10.1/a")
    assert with_doi.fetch_status == FetchStatus.DOWNLOADING
    assert with_doi.fetch_job_id == "job1"
    without_doi = next(p for p in papers if p.doi is None)
    assert without_doi.fetch_status == FetchStatus.SKIPPED

    # compact abstract payload for the model
    assert [p["title"] for p in result["papers"]] == ["Paper A", "Paper B"]
    assert result["papers"][0]["abstract"] == "abs a"
    assert result["papers"][0]["idx"] == 0


def test_make_search_tool_binds_round_and_followup(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = _chat_session(db)
    monkeypatch.setattr(litsearch_client, "search", lambda q, n: [_PAPERS[0]])  # noqa: ARG005
    monkeypatch.setattr(
        litsearch_client, "fetch_async",
        lambda doi, *, url, conversation_id: "job1",  # noqa: ARG005
    )
    parent_id = __import__("uuid").uuid4()

    tool = litsearch_tools.make_search_tool(round=1, followup_of=parent_id)
    tool.handler(db, cs.id, query="cobalt")

    search = db.exec(
        select(LiteratureSearch).where(LiteratureSearch.session_id == cs.id)
    ).one()
    assert search.round == 1
    assert search.followup_of == parent_id


def test_make_search_tool_schema_name_is_literature_search_en() -> None:
    """The `Tool.name` the model calls (and `run_loop` dispatches by) and the
    schema's function name must both be the renamed `literature_search_en` —
    not the old `litsearch_search`."""
    tool = litsearch_tools.make_search_tool()
    assert tool.name == "literature_search_en"
    assert tool.schema["function"]["name"] == "literature_search_en"


def test_litsearch_search_ru_persists_terminal_rows_and_returns_payload(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RU papers skip the whole download cascade: `/search_ru` already
    returned the full text inline, so each `LiteraturePaper` row is created
    already terminal (`fetch_status=SKIPPED`, `fulltext_status=ADDED`) with
    `fulltext_text` populated — no `fetch_async` call."""
    cs = _chat_session(db)
    monkeypatch.setattr(litsearch_client, "search_ru", lambda q, n: _RU_PAPERS)  # noqa: ARG005

    def _boom_fetch_async(*a: Any, **k: Any) -> str:
        raise AssertionError("fetch_async must never be called for RU papers")

    monkeypatch.setattr(litsearch_client, "fetch_async", _boom_fetch_async)

    result = litsearch_tools.litsearch_search_ru(db, cs.id, query="никель")

    search = db.exec(
        select(LiteratureSearch).where(LiteratureSearch.session_id == cs.id)
    ).one()
    assert result["search_id"] == str(search.id)
    assert search.stage == LitStage.FETCHING

    papers = db.exec(
        select(LiteraturePaper).where(LiteraturePaper.search_id == search.id)
    ).all()
    assert len(papers) == 2
    for p in papers:
        assert p.doi is None
        assert p.fetch_status == FetchStatus.SKIPPED
        assert p.fulltext_status == FulltextStatus.ADDED

    with_text = next(p for p in papers if p.title == "Извлечение никеля")
    assert with_text.fulltext_text == "полный текст статьи про никель"
    assert with_text.fulltext_chars == len("полный текст статьи про никель")

    # compact abstract payload for the model, same shape as litsearch_search
    assert [p["title"] for p in result["papers"]] == [
        "Извлечение никеля",
        "Флотация руды",
    ]
    assert result["papers"][0]["abstract"] == "аннотация"
    assert result["papers"][0]["doi"] is None
    assert result["papers"][0]["idx"] == 0


def test_make_search_ru_tool_binds_round_and_followup_and_schema_name(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = _chat_session(db)
    monkeypatch.setattr(litsearch_client, "search_ru", lambda q, n: [_RU_PAPERS[0]])  # noqa: ARG005
    parent_id = __import__("uuid").uuid4()

    tool = litsearch_tools.make_search_ru_tool(round=1, followup_of=parent_id)
    assert tool.name == "literature_search_ru"
    assert tool.schema["function"]["name"] == "literature_search_ru"

    tool.handler(db, cs.id, query="никель")

    search = db.exec(
        select(LiteratureSearch).where(LiteratureSearch.session_id == cs.id)
    ).one()
    assert search.round == 1
    assert search.followup_of == parent_id


def test_ru_only_turn_papers_are_all_terminal_per_reconcile(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An RU-only turn must not stall Phase B's heartbeat wait: every paper
    `litsearch_search_ru` creates is already at a terminal `fetch_status`
    (`SKIPPED`), so `litsearch.reconcile` must report `all_terminal` on the
    very first call — no polling, no re-enqueue."""
    from app.services import litsearch as litsearch_service

    cs = _chat_session(db)
    monkeypatch.setattr(litsearch_client, "search_ru", lambda q, n: _RU_PAPERS)  # noqa: ARG005

    result = litsearch_tools.litsearch_search_ru(db, cs.id, query="никель")
    search_id = uuid.UUID(result["search_id"])

    def _unexpected_job_status(job_id: str) -> dict[str, Any] | None:
        raise AssertionError(f"job_status must not be called (job_id={job_id})")

    monkeypatch.setattr(litsearch_client, "job_status", _unexpected_job_status)

    all_terminal = litsearch_service.reconcile(
        db, search_id, now_ts=0.0, deadline_ts=999.0
    )
    assert all_terminal is True
