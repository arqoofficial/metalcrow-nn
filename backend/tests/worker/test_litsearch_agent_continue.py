import logging
import uuid

import pytest
from sqlmodel import Session, select

from app.models.chat import ChatMessage, ChatRole, ChatSession
from app.models.litsearch import (
    FetchStatus,
    FulltextStatus,
    LiteraturePaper,
    LiteratureSearch,
    LitStage,
)
from app.services import litsearch
from app.services.agent.loop import LoopOutcome
from tests.utils.user import create_random_user


def _seed_turn(
    db: Session,
    *,
    anchor_doi: str | None = "10.1/anchor",
    member_doi: str | None = "10.1/member",
) -> tuple[LiteratureSearch, LiteratureSearch, uuid.UUID]:
    """A turn with two grouped searches (anchor + one member whose
    `followup_of` points at the anchor), each with one fulltext-ready paper —
    mirrors what Phase A produces for a 2-search turn."""
    user = create_random_user(db)
    cs = ChatSession(user_id=user.id, title="agent_continue turn-union test")
    db.add(cs)
    db.commit()
    db.refresh(cs)
    db.add(
        ChatMessage(
            session_id=cs.id, role=ChatRole.USER, content="Как извлекают никель?"
        )
    )
    anchor = LiteratureSearch(
        session_id=cs.id, question="Как извлекают никель?", stage=LitStage.FETCHING
    )
    db.add(anchor)
    db.commit()
    db.refresh(anchor)
    member = LiteratureSearch(
        session_id=cs.id,
        question="А какие есть альтернативы?",
        stage=LitStage.FETCHING,
        followup_of=anchor.id,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    db.add(
        ChatMessage(
            session_id=cs.id,
            role=ChatRole.ASSISTANT,
            content="Ответ по аннотациям",
            message_metadata={
                "litsearch_kind": "abstracts",
                "search_id": str(anchor.id),
            },
        )
    )
    db.add(
        LiteraturePaper(
            search_id=anchor.id,
            title="Anchor paper",
            authors="A",
            abstract="",
            doi=anchor_doi,
            fetch_status=FetchStatus.DONE,
            fulltext_status=FulltextStatus.ADDED,
            fulltext_text="Полный текст статьи из первого поиска.",
        )
    )
    db.add(
        LiteraturePaper(
            search_id=member.id,
            title="Member paper",
            authors="B",
            abstract="",
            doi=member_doi,
            fetch_status=FetchStatus.DONE,
            fulltext_status=FulltextStatus.ADDED,
            fulltext_text="Полный текст статьи из второго поиска.",
        )
    )
    db.commit()
    return anchor, member, cs.id


def _seed(db: Session) -> tuple[LiteratureSearch, uuid.UUID]:
    user = create_random_user(db)
    cs = ChatSession(user_id=user.id, title="agent_continue test")
    db.add(cs)
    db.commit()
    db.refresh(cs)
    db.add(
        ChatMessage(
            session_id=cs.id, role=ChatRole.USER, content="Как извлекают никель?"
        )
    )
    search = LiteratureSearch(
        session_id=cs.id, question="Как извлекают никель?", stage=LitStage.FETCHING
    )
    db.add(search)
    db.commit()
    db.refresh(search)
    db.add(
        ChatMessage(
            session_id=cs.id,
            role=ChatRole.ASSISTANT,
            content="Ответ по аннотациям",
            message_metadata={
                "litsearch_kind": "abstracts",
                "search_id": str(search.id),
            },
        )
    )
    db.add(
        LiteraturePaper(
            search_id=search.id,
            title="P",
            authors="A",
            abstract="",
            fetch_status=FetchStatus.DONE,
            fulltext_status=FulltextStatus.ADDED,
            fulltext_text="Полный текст статьи P про извлечение никеля.",
        )
    )
    db.commit()
    return search, cs.id


def test_agent_continue_persists_fulltext_turn_and_sets_done(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    search, cs_id = _seed(db)

    monkeypatch.setattr(
        litsearch,
        "run_loop",
        lambda *a, **k: LoopOutcome(
            final_text="Ответ по полным текстам",
            tool_calls_made=["litsearch_read_fulltext"],
        ),
    )
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)

    litsearch.agent_continue(db, search.id, cs_id)

    db.refresh(search)
    assert search.stage == LitStage.DONE

    fulltext_msgs = [
        m
        for m in db.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == cs_id)
            .where(ChatMessage.role == ChatRole.ASSISTANT)
        ).all()
        if (m.message_metadata or {}).get("litsearch_kind") == "fulltext"
    ]
    assert len(fulltext_msgs) == 1
    assert fulltext_msgs[0].content == "Ответ по полным текстам"
    assert fulltext_msgs[0].message_metadata["search_id"] == str(search.id)


def test_agent_continue_reseeds_system_user_and_abstract(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    search, cs_id = _seed(db)
    captured: dict = {}

    def fake_run_loop(_session, _chat_session_id, messages, tools, **_kw):  # noqa: ANN001, ANN003
        captured["messages"] = list(messages)
        captured["tool_names"] = [t.name for t in tools]
        return LoopOutcome(final_text="ok")

    monkeypatch.setattr(litsearch, "run_loop", fake_run_loop)
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)

    litsearch.agent_continue(db, search.id, cs_id)

    roles = [m["role"] for m in captured["messages"]]
    # Phase B now injects an explicit "papers downloaded — read them" user turn
    # after the abstract answer.
    assert roles == ["system", "user", "assistant", "user"]
    assert captured["messages"][1]["content"] == "Как извлекают никель?"
    assert captured["messages"][2]["content"] == "Ответ по аннотациям"
    assert "скачаны" in captured["messages"][3]["content"]
    # ...and offers a READ-ONLY toolset (no autonomous follow-up search).
    assert captured["tool_names"] == ["litsearch_read_fulltext"]
    assert "litsearch_search" not in captured["tool_names"]


def test_agent_continue_sets_failed_on_exception(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    search, cs_id = _seed(db)

    def boom(*a, **k):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("loop blew up")

    monkeypatch.setattr(litsearch, "run_loop", boom)
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)

    litsearch.agent_continue(db, search.id, cs_id)  # must not raise

    db.refresh(search)
    assert search.stage == LitStage.FAILED


def test_agent_continue_degraded_persists_explicit_turn(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    search, cs_id = _seed(db)
    monkeypatch.setattr(
        litsearch,
        "run_loop",
        lambda *a, **k: LoopOutcome(final_text=None, degraded=True),  # noqa: ARG005
    )
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)

    litsearch.agent_continue(db, search.id, cs_id)

    db.refresh(search)
    assert search.stage == LitStage.DONE  # settled, not stranded
    msgs = db.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == cs_id)
        .where(ChatMessage.role == ChatRole.ASSISTANT)
    ).all()
    degraded = [
        m for m in msgs if (m.message_metadata or {}).get("mode_used") == "degraded"
    ]
    assert len(degraded) == 1
    assert "LLM недоступен" in degraded[0].content


def test_agent_continue_is_idempotent_on_redelivery(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 2: a redelivered/retried Celery task for the same search must
    not re-run the paid LLM loop or duplicate the persisted fulltext turn —
    the claim guard (FETCHING->READING) makes the second call a no-op."""
    search, cs_id = _seed(db)
    calls = {"n": 0}

    def counting_run_loop(*a, **k):  # noqa: ANN002, ANN003, ARG001
        calls["n"] += 1
        return LoopOutcome(
            final_text="Ответ по полным текстам",
            tool_calls_made=["litsearch_read_fulltext"],
        )

    monkeypatch.setattr(litsearch, "run_loop", counting_run_loop)
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)

    litsearch.agent_continue(db, search.id, cs_id)
    db.refresh(search)
    assert search.stage == LitStage.DONE
    assert calls["n"] == 1

    # Redelivered/retried task for the same search_id: must be a no-op — the
    # search is no longer in FETCHING (it's DONE), so the claim guard's
    # rowcount is 0 and the loop never runs a second time.
    litsearch.agent_continue(db, search.id, cs_id)
    db.refresh(search)
    assert search.stage == LitStage.DONE
    assert calls["n"] == 1  # loop did NOT run again

    fulltext_msgs = [
        m
        for m in db.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == cs_id)
            .where(ChatMessage.role == ChatRole.ASSISTANT)
        ).all()
        if (m.message_metadata or {}).get("litsearch_kind") == "fulltext"
    ]
    assert len(fulltext_msgs) == 1  # not duplicated


def test_agent_continue_commit_failure_persisting_answer_sets_failed_and_reraises(
    db: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """E1: a DB-commit failure while persisting the generated fulltext answer
    must NOT look like a clean success. The LLM already produced an answer
    (`run_loop` succeeded) but the commit that would have saved it fails —
    this must (a) mark the search FAILED (not a DONE with no answer), (b)
    log at CRITICAL, and (c) propagate out of `agent_continue` so the Celery
    task genuinely fails instead of silently discarding the answer while
    still reporting success."""
    search, cs_id = _seed(db)

    monkeypatch.setattr(
        litsearch,
        "run_loop",
        lambda *a, **k: LoopOutcome(final_text="Ответ по полным текстам"),  # noqa: ARG005
    )
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)

    orig_commit = db.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        # call 1 = idempotency claim (FETCHING->READING)
        # call 2 = the fulltext-turn commit inside the try body -> fails here
        # call 3 = the E1 except-branch's own FAILED-stage commit
        # call 4 = the `finally` watchdog's own (now unconditional, turn-wide)
        #          terminal-stage commit
        if calls["n"] == 2:
            raise RuntimeError("simulated commit failure while persisting answer")
        return orig_commit()

    monkeypatch.setattr(db, "commit", flaky_commit)

    with caplog.at_level(logging.CRITICAL):
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            litsearch.agent_continue(db, search.id, cs_id)

    monkeypatch.setattr(db, "commit", orig_commit)
    db.rollback()
    db.refresh(search)

    assert calls["n"] == 4  # the FAILED-stage write inside the E1 handler ran
    assert search.stage == LitStage.FAILED
    assert any(
        r.levelno == logging.CRITICAL and "FAILED TO PERSIST" in r.message
        for r in caplog.records
    )

    # the generated answer must not be silently discarded as a persisted
    # "success" turn — no fulltext ChatMessage should exist
    fulltext_msgs = [
        m
        for m in db.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == cs_id)
            .where(ChatMessage.role == ChatRole.ASSISTANT)
        ).all()
        if (m.message_metadata or {}).get("litsearch_kind") == "fulltext"
    ]
    assert len(fulltext_msgs) == 0


def test_agent_continue_watchdog_does_not_overwrite_failed_from_persist_error(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E1 follow-up: the terminal-stage watchdog `finally` must not clobber a
    FAILED set by the persist-failure handler back to DONE."""
    search, cs_id = _seed(db)
    monkeypatch.setattr(
        litsearch,
        "run_loop",
        lambda *a, **k: LoopOutcome(final_text="ok"),  # noqa: ARG005
    )
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)

    orig_commit = db.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated commit failure while persisting answer")
        return orig_commit()

    monkeypatch.setattr(db, "commit", flaky_commit)

    with pytest.raises(RuntimeError):
        litsearch.agent_continue(db, search.id, cs_id)

    monkeypatch.setattr(db, "commit", orig_commit)
    db.rollback()
    db.refresh(search)

    assert search.stage == LitStage.FAILED  # watchdog did NOT overwrite to DONE


def test_agent_continue_heartbeat_while_downloading_does_not_claim_or_read(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 1: while a paper is still downloading (reconcile reports not all
    terminal) and the deadline hasn't passed, `agent_continue` re-enqueues
    itself and returns WITHOUT claiming the search or running the read loop —
    the search stays FETCHING so the panel keeps showing 'downloading'."""
    search, cs_id = _seed(db)
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: False)  # still downloading
    reenqueued = {"n": 0}
    monkeypatch.setattr(
        litsearch,
        "_reenqueue_heartbeat",
        lambda *a, **k: reenqueued.__setitem__("n", reenqueued["n"] + 1),
    )
    ran = {"loop": 0}
    monkeypatch.setattr(
        litsearch,
        "run_loop",
        lambda *a, **k: (ran.__setitem__("loop", 1), LoopOutcome(final_text="x"))[1],
    )

    litsearch.agent_continue(db, search.id, cs_id)

    db.refresh(search)
    assert search.stage == LitStage.FETCHING  # not claimed while downloading
    assert reenqueued["n"] == 1  # heartbeat re-enqueued
    assert ran["loop"] == 0  # read loop did NOT run yet


def test_agent_continue_no_readable_fulltext_keeps_abstract_no_read_loop(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When every paper is terminal but NONE reached fulltext=ADDED (all
    fetch/extract failed), there is nothing to read: no read loop runs, no
    second answer is persisted, and the search is driven to DONE (the Phase-A
    abstract reply stands)."""
    user = create_random_user(db)
    cs = ChatSession(user_id=user.id, title="no-fulltext")
    db.add(cs)
    db.commit()
    db.refresh(cs)
    search = LiteratureSearch(
        session_id=cs.id, question="q", stage=LitStage.FETCHING
    )
    db.add(search)
    db.commit()
    db.refresh(search)
    db.add(
        LiteraturePaper(
            search_id=search.id,
            title="P",
            authors="A",
            abstract="",
            fetch_status=FetchStatus.FAILED,  # never produced full text
        )
    )
    db.commit()
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)
    ran = {"loop": 0}
    monkeypatch.setattr(
        litsearch,
        "run_loop",
        lambda *a, **k: (ran.__setitem__("loop", 1), LoopOutcome(final_text="x"))[1],
    )

    litsearch.agent_continue(db, search.id, cs.id)

    db.refresh(search)
    assert search.stage == LitStage.DONE
    assert ran["loop"] == 0  # no full text -> no read loop
    fulltext_msgs = [
        m
        for m in db.exec(
            select(ChatMessage).where(ChatMessage.session_id == cs.id)
        ).all()
        if (m.message_metadata or {}).get("litsearch_kind") == "fulltext"
    ]
    assert len(fulltext_msgs) == 0


def test_agent_continue_finally_survives_commit_failure_and_does_not_raise(
    db: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Finding 3: even if the finally watchdog's own attempt to write a
    terminal stage fails (e.g. a dropped connection / stale transaction),
    `agent_continue` must not raise — it rolls back first, then attempts the
    terminal write in its own try/except that only logs (never propagates)."""
    search, cs_id = _seed(db)

    monkeypatch.setattr(
        litsearch, "run_loop", lambda *a, **k: LoopOutcome(final_text="ok")  # noqa: ARG005
    )
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)

    orig_commit = db.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        # call 1 = idempotency claim (FETCHING->READING)
        # call 2 = the fulltext-turn commit inside the try body
        # call 3 = the finally watchdog's terminal (DONE) write
        if calls["n"] == 3:
            raise RuntimeError("simulated dropped connection")
        return orig_commit()

    monkeypatch.setattr(db, "commit", flaky_commit)

    with caplog.at_level(logging.ERROR):
        litsearch.agent_continue(db, search.id, cs_id)  # must not raise

    monkeypatch.setattr(db, "commit", orig_commit)
    db.rollback()
    db.refresh(search)

    assert calls["n"] == 3  # the watchdog write was actually attempted
    # The watchdog's own write failed (by design of this test) and was
    # swallowed rather than propagated — the search is left at the last
    # successfully-committed stage (READING) instead of crashing the worker.
    assert search.stage == LitStage.READING
    assert any("watchdog" in r.message for r in caplog.records)


def test_agent_continue_reads_papers_from_both_grouped_searches(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3 (a): given a turn with two grouped searches (anchor + a
    `followup_of` member), `agent_continue(session, anchor_id, ...)` must
    read papers from BOTH — the injected listing (and thus what the model
    sees via `litsearch_read_fulltext`) is the union, not just the anchor's."""
    anchor, member, cs_id = _seed_turn(db)
    captured: dict = {}

    def fake_run_loop(_session, _chat_session_id, messages, _tools, **_kw):  # noqa: ANN001, ANN003
        captured["messages"] = list(messages)
        return LoopOutcome(
            final_text="Ответ по обоим поискам",
            tool_calls_made=["litsearch_read_fulltext"],
        )

    monkeypatch.setattr(litsearch, "run_loop", fake_run_loop)
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)

    litsearch.agent_continue(db, anchor.id, cs_id)

    listing = captured["messages"][-1]["content"]
    assert "Anchor paper" in listing
    assert "Member paper" in listing
    assert "2 шт" in listing

    db.refresh(anchor)
    db.refresh(member)
    assert anchor.stage == LitStage.DONE
    assert member.stage == LitStage.DONE


def test_agent_continue_dedups_duplicate_doi_across_grouped_searches(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3 (b): a paper with the same DOI returned by two searches in the
    same turn must appear ONCE in the injected listing (first occurrence, in
    `(created_at, id)` order — the anchor's paper wins since it was created
    first)."""
    anchor, member, cs_id = _seed_turn(
        db, anchor_doi="10.1/same", member_doi="10.1/same"
    )
    captured: dict = {}

    def fake_run_loop(_session, _chat_session_id, messages, _tools, **_kw):  # noqa: ANN001, ANN003
        captured["messages"] = list(messages)
        return LoopOutcome(
            final_text="ok", tool_calls_made=["litsearch_read_fulltext"]
        )

    monkeypatch.setattr(litsearch, "run_loop", fake_run_loop)
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)

    litsearch.agent_continue(db, anchor.id, cs_id)

    listing = captured["messages"][-1]["content"]
    assert "1 шт" in listing
    assert "Anchor paper" in listing
    assert "Member paper" not in listing  # deduped out — same DOI, later


def test_agent_continue_watchdog_drives_every_member_to_done(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3 (d): the `finally` watchdog must drive EVERY search of the turn
    (anchor + member) to a terminal stage, not just the anchor."""
    anchor, member, cs_id = _seed_turn(db)
    monkeypatch.setattr(
        litsearch,
        "run_loop",
        lambda *a, **k: LoopOutcome(  # noqa: ARG005
            final_text="ok", tool_calls_made=["litsearch_read_fulltext"]
        ),
    )
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)

    litsearch.agent_continue(db, anchor.id, cs_id)

    db.refresh(anchor)
    db.refresh(member)
    assert anchor.stage == LitStage.DONE
    assert member.stage == LitStage.DONE


def test_agent_continue_watchdog_settles_member_even_when_anchor_fails(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3 (d) follow-up: when the loop blows up, the except-branch only
    marks the ANCHOR as FAILED — the `finally` watchdog is what must still
    settle the member (best-effort flipped to READING earlier) to DONE,
    proving the watchdog iterates `member_ids`, not just `search_id`."""
    anchor, member, cs_id = _seed_turn(db)

    def boom(*a, **k):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("loop blew up")

    monkeypatch.setattr(litsearch, "run_loop", boom)
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)

    litsearch.agent_continue(db, anchor.id, cs_id)  # must not raise

    db.refresh(anchor)
    db.refresh(member)
    assert anchor.stage == LitStage.FAILED
    assert member.stage == LitStage.DONE
