import uuid

import pytest
from sqlmodel import Session, select

from app.models.chat import ChatMessage, ChatRole, ChatSession
from app.models.litsearch import LiteraturePaper, LiteratureSearch, LitStage
from app.schemas.chat import ChatMessageMetadata, ChatMessageRequest, ChatMode
from app.services import chat as chat_service
from app.services.agent.loop import LoopOutcome
from tests.utils.user import create_random_user


def _chat_session(db: Session) -> ChatSession:
    user = create_random_user(db)
    cs = ChatSession(user_id=user.id, title="chat literature test")
    db.add(cs)
    db.commit()
    db.refresh(cs)
    return cs


def _seed_search(db: Session, cs_id: uuid.UUID) -> LiteratureSearch:
    search = LiteratureSearch(session_id=cs_id, question="q")
    db.add(search)
    db.commit()
    db.refresh(search)
    db.add(LiteraturePaper(search_id=search.id, title="P", authors="A", abstract=""))
    db.add(
        ChatMessage(
            session_id=cs_id,
            role=ChatRole.ASSISTANT,
            content="Ответ по аннотациям",
            message_metadata={
                "litsearch_kind": "abstracts",
                "search_id": str(search.id),
            },
        )
    )
    db.commit()
    return search


def test_literature_mode_runs_phase_a_and_dispatches_phase_b(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = _chat_session(db)
    search_holder: dict = {}

    def fake_run_loop(
        session, chat_session_id, _messages, tools, *, first_tool_choice, **_kw
    ):  # noqa: ANN001, ANN003
        assert first_tool_choice == "literature_search_en"
        # withheld: read_fulltext not offered in Phase A; both EN + RU search
        # tools ARE registered (Task C: register both, independent caps).
        assert [t.name for t in tools] == [
            "literature_search_en",
            "literature_search_ru",
        ]
        assert _kw["max_successful_by_tool"] == {
            "literature_search_en": 3,
            "literature_search_ru": 3,
        }
        assert "max_successful_searches" not in _kw
        s = _seed_search(session, chat_session_id)
        search_holder["id"] = s.id
        return LoopOutcome(
            final_text="Ответ по аннотациям",
            tool_calls_made=["literature_search_en"],
            literature_search_ids=[s.id],
        )

    dispatched: list = []
    monkeypatch.setattr(chat_service, "run_loop", fake_run_loop)
    monkeypatch.setattr(
        chat_service,
        "_dispatch_agent_continue",
        lambda session, search_id, cs_id: dispatched.append((search_id, cs_id)),  # noqa: ARG005
    )

    req = ChatMessageRequest(
        content="Как извлекают никель?",
        metadata=ChatMessageMetadata(mode=ChatMode.LITERATURE),
    )
    response = chat_service.answer_message(db, cs.id, req)

    assert response.literature is not None
    assert response.literature.search_id == search_holder["id"]
    assert response.mode_used == "literature"
    assert response.summary == "Ответ по аннотациям"
    assert dispatched == [(search_holder["id"], cs.id)]

    # user row persisted
    user_rows = db.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == cs.id)
        .where(ChatMessage.role == ChatRole.USER)
    ).all()
    assert len(user_rows) == 1


def test_literature_mode_degraded_when_llm_unreachable(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = _chat_session(db)
    monkeypatch.setattr(
        chat_service,
        "run_loop",
        lambda *a, **k: LoopOutcome(final_text=None, degraded=True),  # noqa: ARG005
    )
    monkeypatch.setattr(
        chat_service,
        "_dispatch_agent_continue",
        lambda *a, **k: None,  # noqa: ARG005
    )

    req = ChatMessageRequest(
        content="q", metadata=ChatMessageMetadata(mode=ChatMode.LITERATURE)
    )
    response = chat_service.answer_message(db, cs.id, req)

    assert response.mode_used == "degraded"
    assert "LLM недоступен" in response.summary
    assert response.literature is None


def test_auto_mode_uses_litsearch_when_model_calls_it(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = _chat_session(db)

    def fake_run_loop(
        session, chat_session_id, _messages, _tools, *, first_tool_choice, **_kw
    ):  # noqa: ANN001, ANN003
        assert first_tool_choice is None  # AUTO does not prime
        s = _seed_search(session, chat_session_id)
        return LoopOutcome(
            final_text="Ответ по аннотациям",
            tool_calls_made=["literature_search_en"],
            literature_search_ids=[s.id],
        )

    monkeypatch.setattr(chat_service, "run_loop", fake_run_loop)
    monkeypatch.setattr(
        chat_service,
        "_dispatch_agent_continue",
        lambda *a, **k: None,  # noqa: ARG005
    )

    req = ChatMessageRequest(
        content="никель", metadata=ChatMessageMetadata(mode=ChatMode.AUTO)
    )
    response = chat_service.answer_message(db, cs.id, req)
    assert response.literature is not None
    assert response.mode_used == "literature"


def test_auto_mode_falls_through_to_waterfall_when_no_litsearch(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.schemas.search import SearchMeta, SearchResponse

    cs = _chat_session(db)
    # loop returns a plain text answer with no litsearch tool call -> AUTO must
    # NOT treat it as literature; falls through to ontology/KG waterfall.
    monkeypatch.setattr(
        chat_service,
        "run_loop",
        lambda *a, **k: LoopOutcome(final_text=None, degraded=True),  # noqa: ARG005
    )
    monkeypatch.setattr(chat_service, "_ontology_claims", lambda q, **k: ([], []))  # noqa: ARG005
    monkeypatch.setattr(
        chat_service.agent,
        "hybrid_search",
        lambda s, r: SearchResponse(results=[], total=0, search_meta=SearchMeta()),  # noqa: ARG005
    )
    monkeypatch.setattr(
        chat_service.science_kg_client,
        "rag_query",
        lambda q, **k: None,  # noqa: ARG005
    )

    req = ChatMessageRequest(
        content="привет", metadata=ChatMessageMetadata(mode=ChatMode.AUTO)
    )
    response = chat_service.answer_message(db, cs.id, req)
    assert response.mode_used == "knowledge_graph"
    assert response.literature is None


def test_literature_mode_degraded_after_litsearch_call_persists_explicit_turn(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 1 (CRITICAL fail-loud gap): the model DID call literature_search_en
    (literature_search_id is set — rows exist) but the loop degrades before
    producing the abstract answer turn. Must persist an explicit degraded
    turn (mode_used="degraded", non-empty user-visible text) — never an
    empty/template mode_used="literature" answer — and must NOT dispatch
    Phase B (there is no abstract answer for it to build on)."""
    cs = _chat_session(db)
    search_holder: dict = {}

    def fake_run_loop(
        session, chat_session_id, _messages, tools, *, first_tool_choice, **_kw
    ):  # noqa: ANN001, ANN003
        assert first_tool_choice == "literature_search_en"
        s = _seed_search(session, chat_session_id)
        search_holder["id"] = s.id
        return LoopOutcome(
            final_text=None,
            degraded=True,
            tool_calls_made=["literature_search_en"],
            literature_search_ids=[s.id],
        )

    dispatched: list = []
    monkeypatch.setattr(chat_service, "run_loop", fake_run_loop)
    monkeypatch.setattr(
        chat_service,
        "_dispatch_agent_continue",
        lambda *a, **k: dispatched.append(a),  # noqa: ARG005
    )

    req = ChatMessageRequest(
        content="Как извлекают никель?",
        metadata=ChatMessageMetadata(mode=ChatMode.LITERATURE),
    )
    response = chat_service.answer_message(db, cs.id, req)

    assert response.mode_used == "degraded"
    assert response.summary  # non-empty, never a blank/template answer
    assert "LLM недоступен" in response.summary
    assert response.literature is None
    assert dispatched == []  # no Phase B dispatch when Phase A degraded

    degraded_msgs = [
        m
        for m in db.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == cs.id)
            .where(ChatMessage.role == ChatRole.ASSISTANT)
        ).all()
        if (m.message_metadata or {}).get("mode_used") == "degraded"
    ]
    assert len(degraded_msgs) == 1
    assert degraded_msgs[0].content  # persisted turn is non-empty too


def test_phase_a_dispatch_failure_marks_search_failed_but_returns_abstract(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 4: if dispatching `agent_continue` fails, the search must not
    be silently stranded at FETCHING forever — it's marked FAILED so the
    panel stops polling — while Phase A still returns the abstract answer
    it already persisted (that part of the turn genuinely succeeded)."""
    cs = _chat_session(db)
    search_holder: dict = {}

    def fake_run_loop(
        session, chat_session_id, _messages, tools, *, first_tool_choice, **_kw
    ):  # noqa: ANN001, ANN003
        s = _seed_search(session, chat_session_id)
        search_holder["id"] = s.id
        return LoopOutcome(
            final_text="Ответ по аннотациям",
            tool_calls_made=["literature_search_en"],
            literature_search_ids=[s.id],
        )

    class _BoomSignature:
        def apply_async(self) -> None:
            raise RuntimeError("broker unreachable")

    monkeypatch.setattr(chat_service, "run_loop", fake_run_loop)
    monkeypatch.setattr(
        chat_service.celery_app,
        "signature",
        lambda *a, **k: _BoomSignature(),  # noqa: ARG005
    )

    req = ChatMessageRequest(
        content="Как извлекают никель?",
        metadata=ChatMessageMetadata(mode=ChatMode.LITERATURE),
    )
    response = chat_service.answer_message(db, cs.id, req)

    # The abstract answer still comes back — Phase A itself succeeded.
    assert response.mode_used == "literature"
    assert response.summary == "Ответ по аннотациям"
    assert response.literature is not None
    assert response.literature.search_id == search_holder["id"]

    search = db.get(LiteratureSearch, search_holder["id"])
    assert search is not None
    assert search.stage == LitStage.FAILED


def test_literature_mode_groups_turn_searches_under_anchor_and_dedups_papers(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 2: when the loop reports >1 search id for the turn, Phase A groups
    the non-anchor searches under the anchor via `followup_of`, dispatches
    Phase B once (for the anchor), and reports `paper_count` as the
    DOI-deduped union across all of the turn's searches."""
    cs = _chat_session(db)

    # Anchor search: one paper with a DOI shared with a member paper (deduped)
    # and one paper without a DOI (never deduped).
    anchor = LiteratureSearch(session_id=cs.id, question="q1")
    db.add(anchor)
    db.commit()
    db.refresh(anchor)
    db.add(
        LiteraturePaper(
            search_id=anchor.id,
            title="P1",
            authors="A",
            abstract="",
            doi="10.1/shared",
        )
    )
    db.add(
        LiteraturePaper(search_id=anchor.id, title="P2", authors="A", abstract="")
    )

    # Member search: one paper duplicating the anchor's DOI (deduped away)
    # and one distinct paper (kept).
    member = LiteratureSearch(session_id=cs.id, question="q2")
    db.add(member)
    db.commit()
    db.refresh(member)
    db.add(
        LiteraturePaper(
            search_id=member.id,
            title="P1 dup",
            authors="A",
            abstract="",
            doi="10.1/shared",
        )
    )
    db.add(
        LiteraturePaper(
            search_id=member.id,
            title="P3",
            authors="A",
            abstract="",
            doi="10.1/distinct",
        )
    )
    db.commit()

    def fake_run_loop(*_a, **_kw):  # noqa: ANN001, ANN002, ANN003
        return LoopOutcome(
            final_text="Ответ по аннотациям",
            tool_calls_made=["literature_search_en"],
            literature_search_ids=[anchor.id, member.id],
        )

    dispatched: list = []
    monkeypatch.setattr(chat_service, "run_loop", fake_run_loop)
    monkeypatch.setattr(
        chat_service,
        "_dispatch_agent_continue",
        lambda session, search_id, cs_id: dispatched.append((search_id, cs_id)),  # noqa: ARG005
    )

    req = ChatMessageRequest(
        content="Как извлекают никель?",
        metadata=ChatMessageMetadata(mode=ChatMode.LITERATURE),
    )
    response = chat_service.answer_message(db, cs.id, req)

    db.refresh(anchor)
    db.refresh(member)
    assert anchor.followup_of is None
    assert member.followup_of == anchor.id

    assert dispatched == [(anchor.id, cs.id)]

    assert response.literature is not None
    assert response.literature.search_id == anchor.id
    # 3 distinct papers: shared DOI counted once, plus P2 (no DOI) and P3.
    assert response.literature.paper_count == 3
