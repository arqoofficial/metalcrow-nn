from datetime import timedelta

from sqlmodel import Session

from app.models.chat import ChatSession
from app.models.litsearch import (
    FetchStatus,
    FulltextStatus,
    LiteraturePaper,
    LiteratureSearch,
)
from app.services import litsearch_tools
from tests.utils.user import create_random_user


def _search_with_papers(db: Session, papers: list[LiteraturePaper]) -> LiteratureSearch:
    user = create_random_user(db)
    cs = ChatSession(user_id=user.id, title="fulltext tool test")
    db.add(cs)
    db.commit()
    db.refresh(cs)
    search = LiteratureSearch(session_id=cs.id, question="q?")
    db.add(search)
    db.commit()
    db.refresh(search)
    for p in papers:
        p.search_id = search.id
        db.add(p)
    db.commit()
    return search


def test_read_fulltext_returns_only_added_texts(db: Session) -> None:
    added = LiteraturePaper(
        search_id=None, title="Ready", authors="A", abstract="",
        doi="10.1/ready", fetch_status=FetchStatus.DONE,
        fulltext_status=FulltextStatus.ADDED, fulltext_text="FULL TEXT BODY",
    )
    # A still-downloading paper simply isn't ADDED yet, so it's not returned.
    # (By Phase B, downloads are already reconciled terminal — the read tool no
    # longer polls jobs, so there is no `pending` field.)
    downloading = LiteraturePaper(
        search_id=None, title="Slow", authors="B", abstract="",
        fetch_status=FetchStatus.DOWNLOADING, fetch_job_id="jobZ",
    )
    search = _search_with_papers(db, [added, downloading])

    tool = litsearch_tools.make_read_fulltext_tool(search.id)
    result = tool.handler(db, search.session_id)

    texts = {p["title"]: p["text"] for p in result["papers"]}
    assert texts == {"Ready": "FULL TEXT BODY"}
    assert result["none_available"] is False
    assert "pending" not in result  # read no longer polls downloads


def test_read_fulltext_none_available_when_no_texts(db: Session) -> None:
    skipped = LiteraturePaper(
        search_id=None, title="No PDF", authors="A", abstract="",
        fetch_status=FetchStatus.SKIPPED, fulltext_status=FulltextStatus.NONE,
    )
    search = _search_with_papers(db, [skipped])

    tool = litsearch_tools.make_read_fulltext_tool(search.id)
    result = tool.handler(db, search.session_id)

    assert result["papers"] == []
    assert result["available_idxs"] == []
    assert result["none_available"] is True


def test_read_fulltext_char_capped(db: Session, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(litsearch_tools.settings, "LITSEARCH_FULLTEXT_CHAR_CAP", 5)
    added = LiteraturePaper(
        search_id=None, title="Big", authors="A", abstract="",
        fetch_status=FetchStatus.DONE, fulltext_status=FulltextStatus.ADDED,
        fulltext_text="0123456789",
    )
    search = _search_with_papers(db, [added])

    tool = litsearch_tools.make_read_fulltext_tool(search.id)
    result = tool.handler(db, search.session_id)
    assert result["papers"][0]["text"] == "01234"


def test_read_fulltext_running_budget_stops_and_notes_exhaustion(db: Session, monkeypatch) -> None:  # noqa: ANN001
    # Budget = 20 chars, each paper = 10 chars: reading all returns the first 2
    # (2×10=20 fits), stops before the 3rd, and flags budget exhaustion so the
    # model answers instead of overflowing.
    monkeypatch.setattr(litsearch_tools.settings, "LITSEARCH_READ_BUDGET_CHARS", 20)
    papers = [
        LiteraturePaper(
            search_id=None, title=f"P{i}", authors="A", abstract="",
            doi=f"10.1/p{i}", fetch_status=FetchStatus.DONE,
            fulltext_status=FulltextStatus.ADDED, fulltext_text="0123456789",
        )
        for i in range(3)
    ]
    search = _search_with_papers(db, papers)
    tool = litsearch_tools.make_read_fulltext_tool(search.id)

    result = tool.handler(db, search.session_id)  # read all
    assert len(result["papers"]) == 2
    assert "note" in result and "бюджет" in result["note"].lower()

    # The running total persists across calls: a further read is refused too.
    again = tool.handler(db, search.session_id, idx=2)
    assert again["papers"] == []
    assert "note" in again


def test_read_fulltext_idx_selects_a_single_paper(db: Session) -> None:
    p0 = LiteraturePaper(
        search_id=None, title="P0", authors="A", abstract="",
        fetch_status=FetchStatus.DONE, fulltext_status=FulltextStatus.ADDED,
        fulltext_text="TEXT0",
    )
    p1 = LiteraturePaper(
        search_id=None, title="P1", authors="B", abstract="",
        fetch_status=FetchStatus.DONE, fulltext_status=FulltextStatus.ADDED,
        fulltext_text="TEXT1",
    )
    search = _search_with_papers(db, [p0, p1])

    tool = litsearch_tools.make_read_fulltext_tool(search.id)
    both = tool.handler(db, search.session_id)  # no idx -> all ready papers
    assert len(both["papers"]) == 2
    assert len(both["available_idxs"]) == 2

    # Reading a specific listed idx returns just that one paper.
    target = both["available_idxs"][0]
    one = tool.handler(db, search.session_id, idx=target)
    assert len(one["papers"]) == 1
    assert one["papers"][0]["idx"] == target


def test_read_fulltext_capped_at_max_calls(db: Session) -> None:
    added = LiteraturePaper(
        search_id=None, title="Ready", authors="A", abstract="",
        fetch_status=FetchStatus.DONE, fulltext_status=FulltextStatus.ADDED,
        fulltext_text="BODY",
    )
    search = _search_with_papers(db, [added])

    tool = litsearch_tools.make_read_fulltext_tool(search.id)
    for _ in range(litsearch_tools._MAX_READ_CALLS):
        assert tool.handler(db, search.session_id)["papers"]  # within the cap
    guarded = tool.handler(db, search.session_id)  # one past the cap
    assert guarded["papers"] == []
    assert guarded["note"] == "read limit reached"


def test_read_fulltext_stays_bound_to_its_search(db: Session) -> None:
    """The read tool reads the papers of the search it was bound to, even when a
    newer search exists in the same chat. Phase B is read-only, so no newer
    search is created mid-loop; reads must always hit the user's actual papers
    rather than rebinding to "most recent" (the bug that returned empty text)."""
    original_paper = LiteraturePaper(
        search_id=None, title="Original round", authors="A", abstract="",
        fetch_status=FetchStatus.DONE, fulltext_status=FulltextStatus.ADDED,
        fulltext_text="TEXT FROM ORIGINAL SEARCH",
    )
    original = _search_with_papers(db, [original_paper])

    newer_paper = LiteraturePaper(
        search_id=None, title="Newer round", authors="B", abstract="",
        fetch_status=FetchStatus.DONE, fulltext_status=FulltextStatus.ADDED,
        fulltext_text="TEXT FROM NEWER SEARCH",
    )
    newer = LiteratureSearch(
        session_id=original.session_id, question="newer?", round=1,
        created_at=original.created_at + timedelta(seconds=1),
    )
    db.add(newer)
    db.commit()
    db.refresh(newer)
    newer_paper.search_id = newer.id
    db.add(newer_paper)
    db.commit()

    tool = litsearch_tools.make_read_fulltext_tool(original.id)
    result = tool.handler(db, original.session_id)

    texts = {p["title"]: p["text"] for p in result["papers"]}
    assert texts == {"Original round": "TEXT FROM ORIGINAL SEARCH"}


def test_read_fulltext_invalid_idx_returns_note_not_silent_empty(db: Session) -> None:
    """An out-of-range/hallucinated idx must NOT silently return empty (H1) —
    it returns a note naming the bad idx plus the valid idxs so the model
    retries instead of answering ungrounded."""
    added = LiteraturePaper(
        search_id=None, title="Ready", authors="A", abstract="",
        fetch_status=FetchStatus.DONE, fulltext_status=FulltextStatus.ADDED,
        fulltext_text="FULL TEXT BODY",
    )
    search = _search_with_papers(db, [added])

    tool = litsearch_tools.make_read_fulltext_tool(search.id)
    result = tool.handler(db, search.session_id, idx=99)

    assert result["papers"] == []
    assert result["available_idxs"] != []
    assert result["note"]
    assert "99" in result["note"]


def test_read_fulltext_schema_exposes_only_idx() -> None:
    params = litsearch_tools.READ_FULLTEXT_SCHEMA["function"]["parameters"]
    assert "search_id" not in params.get("properties", {})
    assert set(params.get("properties", {})) == {"idx"}


def test_read_fulltext_reads_union_of_grouped_turn_and_dedups_by_doi(
    db: Session,
) -> None:
    """Task 3 (c): the read tool bound to the anchor id must return the union
    of the anchor's papers AND every `followup_of` member's papers, deduped
    by DOI, with `idx` indexing into that SAME deduped-union order — matching
    what `agent_continue` injects into the transcript."""
    anchor_paper = LiteraturePaper(
        search_id=None, title="Anchor paper", authors="A", abstract="",
        doi="10.1/anchor", fetch_status=FetchStatus.DONE,
        fulltext_status=FulltextStatus.ADDED, fulltext_text="ANCHOR TEXT",
    )
    anchor = _search_with_papers(db, [anchor_paper])

    # A member of the anchor's turn (followup_of == anchor.id), with a paper
    # that has a DISTINCT doi, plus a DUPLICATE-doi paper (same doi as the
    # anchor's paper) which must be deduped out of the union.
    member = LiteratureSearch(
        session_id=anchor.session_id,
        question="follow-up?",
        followup_of=anchor.id,
        created_at=anchor.created_at + timedelta(seconds=1),
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    member_paper = LiteraturePaper(
        search_id=member.id, title="Member paper", authors="B", abstract="",
        doi="10.1/member", fetch_status=FetchStatus.DONE,
        fulltext_status=FulltextStatus.ADDED, fulltext_text="MEMBER TEXT",
    )
    dup_paper = LiteraturePaper(
        search_id=member.id, title="Duplicate of anchor", authors="C", abstract="",
        doi="10.1/anchor", fetch_status=FetchStatus.DONE,
        fulltext_status=FulltextStatus.ADDED, fulltext_text="SHOULD BE DEDUPED OUT",
    )
    db.add(member_paper)
    db.add(dup_paper)
    db.commit()

    tool = litsearch_tools.make_read_fulltext_tool(anchor.id)
    result = tool.handler(db, anchor.session_id)

    titles = {p["title"] for p in result["papers"]}
    assert titles == {"Anchor paper", "Member paper"}
    assert "Duplicate of anchor" not in titles
    assert len(result["papers"]) == 2

    # `idx` indexes into the deduped-union (created_at, id) order: anchor's
    # paper is idx 0, the member's non-duplicate paper is idx 1.
    by_title = {p["title"]: p["idx"] for p in result["papers"]}
    one = tool.handler(db, anchor.session_id, idx=by_title["Member paper"])
    assert len(one["papers"]) == 1
    assert one["papers"][0]["title"] == "Member paper"
    assert one["papers"][0]["text"] == "MEMBER TEXT"


def test_read_fulltext_dedups_doi_less_papers_by_normalized_title(
    db: Session,
) -> None:
    """`_dedup_papers` (renamed from `_dedup_by_doi`) must collapse two
    DOI-less papers (e.g. Cyberleninka/RU results, which never carry a DOI)
    that share the same normalized title, so the SAME RU article returned by
    two searches in one turn isn't read twice. Title normalization collapses
    case + whitespace differences (`" ".join(title.lower().split())`)."""
    anchor_paper = LiteraturePaper(
        search_id=None, title="  Электролитическое  рафинирование Никеля ",
        authors="A", abstract="", doi=None, fetch_status=FetchStatus.SKIPPED,
        fulltext_status=FulltextStatus.ADDED, fulltext_text="ANCHOR TEXT",
    )
    anchor = _search_with_papers(db, [anchor_paper])

    member = LiteratureSearch(
        session_id=anchor.session_id,
        question="follow-up?",
        followup_of=anchor.id,
        created_at=anchor.created_at + timedelta(seconds=1),
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    # Same article title as the anchor's (different case/whitespace) -> must
    # dedup out, even though doi is None on both.
    dup_paper = LiteraturePaper(
        search_id=member.id, title="электролитическое рафинирование никеля",
        authors="B", abstract="", doi=None, fetch_status=FetchStatus.SKIPPED,
        fulltext_status=FulltextStatus.ADDED, fulltext_text="SHOULD BE DEDUPED OUT",
    )
    distinct_paper = LiteraturePaper(
        search_id=member.id, title="Другая статья", authors="C", abstract="",
        doi=None, fetch_status=FetchStatus.SKIPPED,
        fulltext_status=FulltextStatus.ADDED, fulltext_text="DISTINCT TEXT",
    )
    db.add(dup_paper)
    db.add(distinct_paper)
    db.commit()

    tool = litsearch_tools.make_read_fulltext_tool(anchor.id)
    result = tool.handler(db, anchor.session_id)

    titles = {p["title"] for p in result["papers"]}
    assert titles == {"  Электролитическое  рафинирование Никеля ", "Другая статья"}
    assert len(result["papers"]) == 2
