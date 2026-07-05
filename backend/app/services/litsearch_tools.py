"""In-process litsearch tools invoked by the agent loop (spec §2.3). Reuses the
existing OpenAlex search + fetch machinery from `litsearch.py`; adds the
loop-facing handlers and their OpenAI function schemas."""

import logging
import uuid
from typing import Any

from sqlmodel import Session, col, select

from app.core.config import settings
from app.models.litsearch import (
    FetchStatus,
    FulltextStatus,
    LiteraturePaper,
    LiteratureSearch,
    LitStage,
)
from app.services import litsearch, litsearch_client
from app.services.agent.loop import Tool

logger = logging.getLogger(__name__)

SEARCH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "literature_search_en",
        "description": (
            "Search the international / English-language scholarly literature "
            "(OpenAlex) for papers relevant to a query. Returns paper abstracts "
            "you can use to answer. Use a SHORT focused keyword phrase (3-5 "
            "words, ONE concept) with canonical domain terminology — NOT a full "
            "sentence, NOT geography, NOT vague qualifiers, which dilute "
            "relevance. Call it several times with different focused queries to "
            "cover a broad question, and change the TERMS (don't repeat the "
            "same phrase) if results are off-topic."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Short focused keyword phrase (3-5 words, one concept, "
                        "canonical field terms). Not a sentence."
                    ),
                }
            },
            "required": ["query"],
        },
    },
}


def litsearch_search(
    session: Session,
    chat_session_id: uuid.UUID,
    *,
    query: str,
    round: int = 0,
    followup_of: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Search OpenAlex, persist the `LiteratureSearch` + `LiteraturePaper` rows,
    fire off background PDF fetches for every fetchable paper, and return a
    compact abstract-only payload for the model to reason over."""
    papers = litsearch_client.search(query, settings.LITSEARCH_MAX_RESULTS)

    search = LiteratureSearch(
        session_id=chat_session_id,
        question=query,
        round=round,
        followup_of=followup_of,
        stage=LitStage.SEARCHING,
    )
    session.add(search)
    session.commit()
    session.refresh(search)

    paper_rows = [litsearch._paper_from_openalex(p) for p in papers]
    for row in paper_rows:
        row.search_id = search.id
        session.add(row)
    session.commit()
    for row in paper_rows:
        session.refresh(row)

    for row in paper_rows:
        if row.fetch_status == FetchStatus.SKIPPED or row.doi is None:
            continue
        job_id = litsearch_client.fetch_async(
            row.doi, url=row.pdf_url, conversation_id=str(search.id)
        )
        if job_id:
            row.fetch_status = FetchStatus.DOWNLOADING
            row.fetch_job_id = job_id
            row.object_key = f"{job_id}.pdf"
        else:
            logger.warning(
                "fetch_async rejected/unreachable for DOI %s (search %s); leaving PENDING",
                row.doi,
                search.id,
            )
        session.add(row)

    search.stage = LitStage.FETCHING
    session.add(search)
    session.commit()

    return {
        "search_id": str(search.id),
        "papers": [
            {
                "idx": i,
                "title": r.title,
                "authors": r.authors,
                "year": r.year,
                "doi": r.doi,
                "abstract": r.abstract,
            }
            for i, r in enumerate(paper_rows)
        ],
    }


def make_search_tool(
    *, round: int = 0, followup_of: uuid.UUID | None = None
) -> Tool:
    """Binds `round`/`followup_of` into a `Tool` whose handler adapts the
    generic `(session, chat_session_id, **args)` shape `run_loop` invokes.
    The `Tool.name` is `literature_search_en` — it must match `SEARCH_SCHEMA`'s
    function name, since that's the name the model calls and `run_loop`
    dispatches by (the Python handler function is still `litsearch_search`)."""

    def handler(session: Session, chat_session_id: uuid.UUID, **kwargs: Any) -> dict[str, Any]:
        return litsearch_search(
            session,
            chat_session_id,
            query=kwargs.get("query", ""),
            round=round,
            followup_of=followup_of,
        )

    return Tool(name="literature_search_en", schema=SEARCH_SCHEMA, handler=handler)


SEARCH_RU_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "literature_search_ru",
        "description": (
            "Поиск в русскоязычной научной литературе (Cyberleninka). Запрос — "
            "короткая ключевая фраза на русском."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Короткая ключевая фраза на русском (3-5 слов, одна "
                        "идея), а не целое предложение."
                    ),
                }
            },
            "required": ["query"],
        },
    },
}


def litsearch_search_ru(
    session: Session,
    chat_session_id: uuid.UUID,
    *,
    query: str,
    round: int = 0,
    followup_of: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Search Cyberleninka (RU) via article-fetcher's `/search_ru`, persist the
    `LiteratureSearch` + `LiteraturePaper` rows, and return a compact
    abstract-only payload for the model to reason over — same shape as
    `litsearch_search`.

    Unlike `litsearch_search`, RU papers never enter the async download
    cascade: `/search_ru` already fetched each article's FULL text inline
    (article-fetcher's cyberleninka client fetches the article pages before
    responding), so every row is created already terminal —
    `fetch_status=SKIPPED` (nothing left to fetch) and
    `fulltext_status=ADDED` with `fulltext_text` populated. `reconcile`
    already treats `SKIPPED` as a terminal fetch status (`_TERMINAL_FETCH_STATUSES`
    in litsearch.py), so an RU-only turn's heartbeat wait sees `all_terminal`
    immediately and Phase B proceeds straight to the read step — no polling,
    no `fetch_async` call."""
    papers = litsearch_client.search_ru(query, settings.LITSEARCH_MAX_RESULTS)

    search = LiteratureSearch(
        session_id=chat_session_id,
        question=query,
        round=round,
        followup_of=followup_of,
        stage=LitStage.FETCHING,
    )
    session.add(search)
    session.commit()
    session.refresh(search)

    paper_rows: list[LiteraturePaper] = []
    for p in papers:
        fulltext = p.get("fulltext") or ""
        row = LiteraturePaper(
            search_id=search.id,
            doi=None,
            title=p.get("title") or "(без названия)",
            authors=p.get("authors") or "Unknown",
            year=p.get("year"),
            abstract=p.get("abstract") or "",
            pdf_url=None,
            citation_count=None,
            fetch_status=FetchStatus.SKIPPED,
            fulltext_status=FulltextStatus.ADDED,
            fulltext_text=fulltext,
            fulltext_chars=len(fulltext),
        )
        paper_rows.append(row)
        session.add(row)
    session.commit()
    for row in paper_rows:
        session.refresh(row)

    return {
        "search_id": str(search.id),
        "papers": [
            {
                "idx": i,
                "title": r.title,
                "authors": r.authors,
                "year": r.year,
                "doi": r.doi,
                "abstract": r.abstract,
            }
            for i, r in enumerate(paper_rows)
        ],
    }


def make_search_ru_tool(
    *, round: int = 0, followup_of: uuid.UUID | None = None
) -> Tool:
    """Binds `round`/`followup_of` into a `Tool` whose handler adapts the
    generic `(session, chat_session_id, **args)` shape `run_loop` invokes.
    Mirrors `make_search_tool`, but for the RU (Cyberleninka) search."""

    def handler(session: Session, chat_session_id: uuid.UUID, **kwargs: Any) -> dict[str, Any]:
        return litsearch_search_ru(
            session,
            chat_session_id,
            query=kwargs.get("query", ""),
            round=round,
            followup_of=followup_of,
        )

    return Tool(name="literature_search_ru", schema=SEARCH_RU_SCHEMA, handler=handler)


READ_FULLTEXT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "litsearch_read_fulltext",
        "description": (
            "Read the full text of the papers already downloaded for this "
            "search. Pass `idx` to read one specific paper (the indices are "
            "listed in the message announcing the downloaded papers); omit "
            "`idx` to read all of them at once. Call it again with a different "
            "`idx` to read another paper. Answer the user from the full texts "
            "you read."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "idx": {
                    "type": "integer",
                    "description": (
                        "Index of a single paper to read (as listed). Omit to "
                        "read every downloaded paper at once."
                    ),
                }
            },
        },
    },
}

# Floor for the per-loop read-call budget. The effective cap is raised to cover
# the whole turn's union (the model routinely emits ONE response with a parallel
# read_fulltext call per paper — 9+ at once for a multi-search turn — and each
# counts as a call here). A fixed 6 truncated the union: papers past the 6th
# returned an empty "read limit reached" instead of their text, so the model
# never received them. The cap exists only as an anti-infinite-loop backstop;
# max_iters + the exhausted-answer nudge are the real loop terminators.
_MAX_READ_CALLS = 6
_READ_CALL_BUFFER = 4  # allow a few re-reads beyond one pass over the union


def make_read_fulltext_tool(search_id: uuid.UUID) -> Tool:
    """Read tool bound to THIS search's papers (spec §2.3). The model passes at
    most an `idx` to pick one paper and cannot target another search.

    Papers are indexed by creation order — a stable ordering that matches the
    listing `agent_continue` injects into the transcript, so `idx` means the same
    thing on both sides. Capped at `_MAX_READ_CALLS` calls per loop.

    Bound strictly to `search_id`'s WHOLE TURN (the anchor plus every search
    grouped under it via `followup_of` — see `litsearch._turn_search_ids`),
    deduped via `litsearch._dedup_papers` (DOI when present, else normalized
    title — collapses duplicate DOI-less Cyberleninka/RU papers too). Phase B
    no longer offers `literature_search_en`/`literature_search_ru`, so no newer
    search can be created mid-loop for a "most recent" lookup to (wrongly)
    rebind onto — reads always hit the papers the user actually searched for
    this turn. By the time this runs, `agent_continue` has already driven
    every paper to a terminal fetch status (heartbeat wait), so there is
    nothing left to reconcile here."""
    call_count = {"n": 0}
    chars_read = {"n": 0}  # running total of full-text chars handed to the model

    def handler(session: Session, _chat_session_id: uuid.UUID, **kwargs: Any) -> dict[str, Any]:
        # Same turn-union + DOI-dedup + (created_at, id) ordering as
        # `agent_continue` uses to build the listing it injects into the
        # transcript, so the `idx` values mean the same thing on both sides.
        member_ids = litsearch._turn_search_ids(session, search_id)
        raw_papers = session.exec(
            select(LiteraturePaper)
            .where(col(LiteraturePaper.search_id).in_(member_ids))
            .order_by(col(LiteraturePaper.created_at), col(LiteraturePaper.id))
        ).all()
        papers = litsearch._dedup_papers(raw_papers)
        ready = [
            (i, p)
            for i, p in enumerate(papers)
            if p.fulltext_status == FulltextStatus.ADDED and p.fulltext_text
        ]
        available_idxs = [i for i, _ in ready]

        # Effective cap scales with the union so a single all-at-once parallel
        # read (one call per ready paper) never trips the limit; the floor keeps
        # small turns unchanged.
        read_limit = max(_MAX_READ_CALLS, len(ready) + _READ_CALL_BUFFER)
        if call_count["n"] >= read_limit:
            return {
                "papers": [],
                "available_idxs": available_idxs,
                "none_available": not ready,
                "note": "read limit reached",
            }
        call_count["n"] += 1

        idx = kwargs.get("idx")
        if idx is not None:
            # Coerce a stringified idx ("0") so it doesn't silently mismatch the
            # int index.
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                idx = None
        if idx is not None:
            selected = [(i, p) for i, p in ready if i == idx]
            if not selected:
                # Invalid/hallucinated idx: don't silently return nothing (which
                # would let the model answer ungrounded yet still count as a
                # read). Signal the valid indices so it retries, and log so ops
                # sees the mis-index.
                logger.warning(
                    "read_fulltext: idx %s not among available %s (search %s)",
                    idx,
                    available_idxs,
                    search_id,
                )
                return {
                    "papers": [],
                    "available_idxs": available_idxs,
                    "none_available": not ready,
                    "note": f"idx {idx} not available; valid idxs: {available_idxs}",
                }
        else:
            selected = ready

        # Running read budget: hand back full texts greedily until the total
        # chars returned across ALL reads this turn would exceed
        # LITSEARCH_READ_BUDGET_CHARS, then STOP including papers and tell the
        # model to answer now. This bounds the model's context so a big union
        # can't overflow / time out the read call (the degrade cause), while
        # still letting the model choose which papers to read. Each paper is
        # capped at FULLTEXT_CHAR_CAP first, then counted against the budget.
        per_cap = settings.LITSEARCH_FULLTEXT_CHAR_CAP
        budget = settings.LITSEARCH_READ_BUDGET_CHARS
        out_papers: list[dict[str, Any]] = []
        exhausted = False
        for i, p in selected:
            text = (p.fulltext_text or "")[:per_cap]
            if chars_read["n"] + len(text) > budget:
                # This paper would push us over the budget: don't include it (or
                # any further ones), and flag exhaustion so the model answers.
                exhausted = True
                break
            chars_read["n"] += len(text)
            out_papers.append({"idx": i, "title": p.title, "doi": p.doi, "text": text})

        result: dict[str, Any] = {
            "papers": out_papers,
            "available_idxs": available_idxs,
            "none_available": not ready,
        }
        if exhausted:
            result["note"] = (
                "Бюджет чтения исчерпан. Больше не вызывай инструменты — дай "
                "развёрнутый ответ пользователю по уже прочитанным полным текстам."
            )
        return result

    return Tool(name="litsearch_read_fulltext", schema=READ_FULLTEXT_SCHEMA, handler=handler)
