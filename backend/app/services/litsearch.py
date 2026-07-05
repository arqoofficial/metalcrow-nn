"""Оркестрация litsearch tool loop'а (spec §2.4). Phase B —
`agent_continue`, фоновая Celery-задача: пере-собирает `[system, user,
abstract]` из БД, гоняет model-driven tool loop (`run_loop`) с
`litsearch_read_fulltext` + `litsearch_search`, персистит каждый full-text
turn как `ChatMessage(litsearch_kind:"fulltext")` и ВСЕГДА доводит
`search.stage` до терминального значения (try/finally-watchdog, spec §2.11).

Фаза A (синхронный ответ по аннотациям) и роутинг живут в `chat.py`; здесь —
только фоновая фаза + переиспользуемые OpenAlex-хелперы (`_paper_from_openalex`,
`reconcile`, `_mark_fetched`, `add_to_database`).

Все внешние зависимости импортируются на уровне модуля, чтобы тесты могли их
monkeypatch'ить — тот же паттерн, что у `chat.py`/`ontology_client`.
"""

import logging
import time
import uuid
from collections.abc import Sequence
from datetime import UTC
from typing import Any

from sqlmodel import Session, col, select, update

from app.core.config import settings
from app.models.chat import ChatMessage, ChatRole
from app.models.documents import Document, ProcessingLevel
from app.models.ingest import IngestStatus, IngestTask
from app.models.litsearch import (
    FetchStatus,
    FulltextStatus,
    LiteraturePaper,
    LiteratureSearch,
    LitIngestStatus,
    LitStage,
)
from app.services import litsearch_client, pdf_text, storage, tasks
from app.services.agent.loop import LoopOutcome, run_loop

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Ты — научный ассистент-металлург. Отвечай по-русски, опираясь только на "
    "полные тексты статей, которые возвращает инструмент litsearch_read_fulltext. "
    "Прочитай тексты найденных статей — по одной через `idx` или все сразу — и "
    "дай пользователю развёрнутый обоснованный ответ. Не выдумывай факты."
)
_DEGRADED_TEXT = "LLM недоступен — ответ по полным текстам не сформирован."
# Fallback nudge for the read loop's forced-answer path (run_loop's max_iters /
# markup-leak fallback). DeepSeek with `tool_choice="auto"` keeps emitting
# read_fulltext tool calls and never transitions to prose on its own, so the
# loop always exhausts `max_iters`; without this "stop and answer" instruction
# the forced `tool_choice="none"` call comes back empty or as leaked ｜DSML｜
# tool-call markup and the whole turn degrades. Mirrors Phase A's
# `_LITSEARCH_EXHAUSTED_MSG`. Verified live: with this nudge the same transcript
# yields a real grounded Russian answer.
_READ_EXHAUSTED_MSG = (
    "Ты уже прочитал доступные полные тексты. Больше не вызывай инструменты. "
    "Дай развёрнутый ответ пользователю на русском языке, опираясь только на "
    "прочитанные полные тексты. Если в них нет релевантной информации по "
    "вопросу — честно скажи об этом и кратко перечисли, что удалось найти. "
    "Не выдумывай факты."
)

# Provenance seam (task 10 follow-up): once litsearch PDFs flow into the
# shared L1 parse -> graph/ontology extraction (enabled by
# `settings.LITSEARCH_INGEST_ENABLED`), Nornickel-kg (owner of that
# extraction) needs provenance (source/DOI/OpenAlex-id/language) on the
# `Document` row to bind attribution + dedup. Attaching it to
# `experiments.documents` needs new columns there, and that table — plus the
# parser/graph/ontology code that reads it — belongs to other teams; adding
# those columns is pending OSN's cross-team sign-off, coordinated separately.
# Until then, the provenance already lives on the litsearch-owned
# `LiteraturePaper` row (it carries `doi` today; queryable from the
# `Document` via `document_id`). Flipping this flag on (plus adding the
# `Document` columns, done by the schema owners) is the whole enablement —
# see the dormant block in `add_to_database` and `_paper_provenance` below.
_ATTACH_PROVENANCE_TO_DOCUMENT = False

_TERMINAL_FETCH_STATUSES = {
    FetchStatus.DONE,
    FetchStatus.FAILED,
    FetchStatus.SKIPPED,
}


def _turn_search_ids(session: Session, anchor_id: uuid.UUID) -> list[uuid.UUID]:
    """All search ids in the anchor's turn: the anchor plus every search whose
    followup_of points at it (the turn-group key). Deterministic order:
    anchor first, then members by (created_at, id)."""
    members = session.exec(
        select(LiteratureSearch)
        .where(LiteratureSearch.followup_of == anchor_id)
        .order_by(col(LiteratureSearch.created_at), col(LiteratureSearch.id))
    ).all()
    return [anchor_id] + [m.id for m in members]


def _dedup_papers(papers: Sequence[LiteraturePaper]) -> list[LiteraturePaper]:
    """Keep first occurrence per dedup key (in the given order): `doi`
    (lowercased) when present, else a normalized title (whitespace-collapsed,
    lowercased), else always kept (both empty — nothing to key on).

    Cyberleninka (RU) papers are DOI-less, so keying purely on DOI (the old
    `_dedup_by_doi` behavior) would keep EVERY RU paper, even the SAME article
    returned by two RU searches in a turn — it would then get read twice.
    Falling back to a normalized title for the DOI-less case collapses those
    duplicates while leaving DOI-keyed (OpenAlex/EN) behavior unchanged; a
    mixed EN(DOI)/RU(title) union still dedups each side correctly since the
    two key kinds are namespaced (`doi:`/`title:`) and can't collide.

    Preserves ordering so `idx` is stable across Phase B and the read tool."""
    seen: set[str] = set()
    out: list[LiteraturePaper] = []
    for p in papers:
        if p.doi:
            key = f"doi:{p.doi.lower()}"
        else:
            normalized_title = " ".join((p.title or "").lower().split())
            key = f"title:{normalized_title}" if normalized_title else ""
        if not key:
            out.append(p)
        elif key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _paper_from_openalex(paper: dict[str, Any]) -> LiteraturePaper:
    """Строит `LiteraturePaper` (без `search_id`, проставляется вызывающей
    стороной) из OpenAlex-словаря `/search`. Любое поле может отсутствовать
    или быть `None` — недостающие обязательные строковые поля модели
    (`title`/`authors`/`abstract`) получают безопасные дефолты.

    Статья без DOI И без pdf_url физически недокачиваема (article-fetcher
    `/fetch` требует DOI, прямой URL — не для всех источников) -> сразу
    `SKIPPED`, никогда не будет фетчиться."""
    doi = paper.get("doi") or None
    pdf_url = paper.get("pdf_url") or None
    fetch_status = (
        FetchStatus.SKIPPED if doi is None and pdf_url is None else FetchStatus.PENDING
    )
    return LiteraturePaper(
        doi=doi,
        title=paper.get("title") or "(без названия)",
        authors=paper.get("authors") or "Unknown",
        year=paper.get("year"),
        abstract=paper.get("abstract") or "",
        pdf_url=pdf_url,
        citation_count=paper.get("citation_count"),
        fetch_status=fetch_status,
    )


def _mark_fetched(paper: LiteraturePaper, job_status_resp: dict[str, Any]) -> None:
    """Handles a `job_status` response of `status=="done"`: downloads the PDF
    bytes from storage and extracts full text. The download+extract step is
    deliberately wrapped in a broad `except Exception` (not just
    `PdfExtractError`/`StorageObjectNotFoundError`) — a fetched-but-unusable
    PDF (missing object, corrupt bytes triggering an exotic pypdf exception
    that `pdf_text.extract_text`'s own narrower catch still lets through, see
    T5 carry-forward) must degrade to `fulltext_status=FAILED`, not crash the
    monitor task and leave the paper (and the whole search) stuck non-terminal
    forever."""
    object_key = job_status_resp.get("object_key")
    if not isinstance(object_key, str):
        object_key = paper.object_key

    stream = None
    try:
        if object_key is None:
            raise ValueError(
                f"job {paper.fetch_job_id} reported done but no object_key "
                f"(response nor stored paper row) for paper {paper.id}"
            )
        paper.object_key = object_key
        stream = storage.open_document(minio_key=object_key)
        data = b"".join(stream.stream(8192))
        text = pdf_text.extract_text(
            data, char_cap=settings.LITSEARCH_FULLTEXT_CHAR_CAP
        )
    except Exception:
        logger.warning(
            "fulltext fetch/extract failed for paper %s (job %s, key %s)",
            paper.id,
            paper.fetch_job_id,
            object_key,
            exc_info=True,
        )
        paper.fetch_status = FetchStatus.DONE
        paper.fulltext_status = FulltextStatus.FAILED
        paper.fulltext_chars = 0
        paper.fulltext_text = None
    else:
        paper.fetch_status = FetchStatus.DONE
        paper.fulltext_status = FulltextStatus.ADDED
        paper.fulltext_chars = len(text)
        paper.fulltext_text = text
    finally:
        # `storage.open_document` returns a live MinIO connection (or the
        # `FakeStorage` stub, which mirrors the same interface) — release it
        # back to the pool regardless of outcome, or a long-lived worker
        # leaks connections over its lifetime.
        if stream is not None:
            stream.close()
            stream.release_conn()


def reconcile(
    session: Session, search_id: uuid.UUID, *, now_ts: float, deadline_ts: float
) -> bool:
    """Fetch-reconcile core logic (called by `agent_continue` / the
    `litsearch_read_fulltext` tool): advances every `LiteraturePaper` of
    `search_id` towards a terminal `fetch_status` (`DONE`/`FAILED`/`SKIPPED`)
    and reports whether they all got there.

    1. Any leftover `PENDING` paper (never got an async job — DOI-less, or
       `fetch_async` was rejected/unreachable when the search was created) can
       never be fetched -> `SKIPPED` immediately, so it doesn't dangle forever
       (T7 carry-forward).
    2. Every `DOWNLOADING` paper is polled via `litsearch_client.job_status`:
       `"done"` -> fetch+extract full text (`_mark_fetched`, `DONE`/`ADDED`
       on success, `DONE`/`FAILED` on any fetch/extract error); `"failed"` ->
       `FAILED`/`FAILED`; anything else (`"pending"`/`"running"`/unreachable)
       leaves the paper `DOWNLOADING` for the next heartbeat.
    3. Any paper still `DOWNLOADING` once `now_ts > deadline_ts` (a stuck
       job) is force-`FAILED`/`FAILED` so the search can proceed with
       whatever fulltext is ready rather than waiting forever.

    Returns `True` iff every paper's `fetch_status` is now terminal.
    """
    papers = session.exec(
        select(LiteraturePaper).where(LiteraturePaper.search_id == search_id)
    ).all()

    for paper in papers:
        if paper.fetch_status == FetchStatus.PENDING:
            paper.fetch_status = FetchStatus.SKIPPED
            session.add(paper)

    for paper in papers:
        if paper.fetch_status != FetchStatus.DOWNLOADING or not paper.fetch_job_id:
            continue
        job_status_resp = litsearch_client.job_status(paper.fetch_job_id)
        status = (job_status_resp or {}).get("status")
        if status == "done":
            _mark_fetched(paper, job_status_resp or {})
            session.add(paper)
        elif status == "failed":
            logger.warning(
                "fetch job %s failed for paper %s: %s",
                paper.fetch_job_id,
                paper.id,
                (job_status_resp or {}).get("error"),
            )
            paper.fetch_status = FetchStatus.FAILED
            paper.fulltext_status = FulltextStatus.FAILED
            session.add(paper)
        # else: still pending/running (or job_status unreachable -> None) —
        # leave DOWNLOADING for the next heartbeat, subject to the deadline
        # sweep below.

    if now_ts > deadline_ts:
        for paper in papers:
            if paper.fetch_status == FetchStatus.DOWNLOADING:
                logger.warning(
                    "fetch job %s past deadline for paper %s; forcing FAILED",
                    paper.fetch_job_id,
                    paper.id,
                )
                paper.fetch_status = FetchStatus.FAILED
                paper.fulltext_status = FulltextStatus.FAILED
                session.add(paper)

    session.commit()

    return all(paper.fetch_status in _TERMINAL_FETCH_STATUSES for paper in papers)


def _reenqueue_heartbeat(
    session: Session, search_id: uuid.UUID, chat_session_id: uuid.UUID
) -> bool:
    """Re-enqueue this Phase-B task after `LITSEARCH_HEARTBEAT_SECONDS` so it
    polls the still-in-flight downloads again without blocking a worker slot.
    Returns True on success, False if the re-enqueue failed.

    Fail-loud (spec §2.7): if the broker is unreachable and the re-enqueue
    fails, the search would otherwise hang in FETCHING and the panel would poll
    forever — so mark it FAILED (mirrors `chat._dispatch_agent_continue`)."""
    try:
        tasks.celery_app.signature(
            "litsearch.agent_continue",
            args=[str(search_id), str(chat_session_id)],
        ).apply_async(countdown=settings.LITSEARCH_HEARTBEAT_SECONDS)
        return True
    except Exception:
        logger.critical(
            "agent_continue: failed to re-enqueue heartbeat for search %s; "
            "marking stage=FAILED so the panel stops polling",
            search_id,
            exc_info=True,
        )
        search = session.get(LiteratureSearch, search_id)
        if search is not None:
            search.stage = LitStage.FAILED
            session.add(search)
            session.commit()
        return False


def agent_continue(
    session: Session, search_id: uuid.UUID, chat_session_id: uuid.UUID
) -> None:
    """Phase B (spec §2.4): wait (non-blocking) for the found papers to finish
    downloading, then inject a "papers are downloaded — read them" turn and run a
    READ-ONLY tool loop (`litsearch_read_fulltext` only) so the model reads the
    full texts it fetched and answers the user thoroughly. ALWAYS drives `stage`
    to a terminal value (try/finally watchdog, spec §2.11).

    `run_loop` and `reconcile` are referenced as module-level names so tests can
    monkeypatch them on `litsearch`. `litsearch_tools` is imported function-locally
    to avoid the import cycle (`litsearch_tools` imports `litsearch`)."""
    from app.services import litsearch_tools  # local import: avoids import cycle

    search = session.get(LiteratureSearch, search_id)
    if search is None:
        logger.error("agent_continue: search %s not found", search_id)
        return
    if search.stage in (LitStage.DONE, LitStage.FAILED):
        return  # already terminal — nothing to do (idempotent)

    # --- Step 1: wait (non-blocking) for the PDFs to finish downloading -------
    # The deadline is anchored to the search's creation so it actually elapses (a
    # per-call `now + TIMEOUT` would sit forever in the future). While papers are
    # still downloading and the deadline hasn't passed, re-enqueue THIS task after
    # a short delay and release the worker instead of blocking it — the search
    # stays FETCHING and the panel keeps showing "downloading". Only once every
    # paper is terminal (or the deadline force-fails the stragglers) do we proceed
    # to the read phase. `reconcile` is idempotent, so overlapping heartbeats are
    # harmless.
    now = time.time()
    created = search.created_at
    if created is None:
        logger.warning(
            "agent_continue: search %s has no created_at; proceeding without "
            "download wait",
            search_id,
        )
        deadline_ts = now - 1.0
    else:
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        deadline_ts = created.timestamp() + settings.LITSEARCH_FETCH_TIMEOUT

    # `member_ids` (anchor + every search whose `followup_of` points at it)
    # must be resolved before the watchdog-bearing `try` below so `finally`
    # can drive every member of the turn to a terminal stage, not just the
    # anchor.
    member_ids = _turn_search_ids(session, search_id)

    if search.stage == LitStage.FETCHING:
        # `all(...)` short-circuits on the first `False`, which would skip
        # `reconcile`'s side effects (fetch polling/terminalization) on later
        # members once one returns not-yet-terminal — materialize the list
        # first so every member is reconciled regardless of the others'
        # outcome.
        results = [
            reconcile(session, mid, now_ts=now, deadline_ts=deadline_ts)
            for mid in member_ids
        ]
        all_terminal = all(results)
        if not all_terminal and now < deadline_ts:
            if _reenqueue_heartbeat(session, search_id, chat_session_id):
                logger.info(
                    "agent_continue: turn %s still downloading — heartbeat "
                    "re-enqueued in %ss",
                    search_id,
                    settings.LITSEARCH_HEARTBEAT_SECONDS,
                )
            return

    # --- Step 2: idempotency claim (FETCHING -> READING) ----------------------
    # Downloads are done (or deadline-forced terminal). Atomically claim the
    # search so exactly one delivery runs the paid read loop; a redelivered or
    # overlapping heartbeat sees rowcount 0 here and no-ops. Flipping
    # FETCHING->READING (`LitStage.READING`, otherwise unused) is the
    # "in-progress, claimed by this delivery" marker, not a UI-visible stage.
    # If another delivery already claimed it, or the search is already terminal
    # /still SEARCHING, `rowcount` is 0 and this call is a no-op.
    claim_result = session.exec(
        update(LiteratureSearch)
        .where(LiteratureSearch.id == search_id)  # type: ignore[arg-type]
        .where(LiteratureSearch.stage == LitStage.FETCHING)  # type: ignore[arg-type]
        .values(stage=LitStage.READING)
    )
    session.commit()
    if claim_result.rowcount == 0:
        logger.info(
            "agent_continue: search %s already claimed or not in FETCHING "
            "(redelivered/retried task or terminal search) — skipping as a "
            "no-op (idempotency guard)",
            search_id,
        )
        return

    if member_ids[1:]:
        # Best-effort: flip the other turn members to READING too so the
        # panel reflects the claim across the whole turn, not just the
        # anchor. The exactly-once guarantee lives entirely in the anchor
        # claim above; this is cosmetic and never re-checked.
        #
        # MUST be truly best-effort: this runs AFTER the anchor was already
        # claimed READING (committed above) but BEFORE the try/finally whose
        # watchdog guarantees a terminal stage. If this commit raised and
        # propagated, the anchor would be stranded at READING forever (the
        # watchdog never runs; every redelivery no-ops at the FETCHING claim)
        # — the exact spin-forever failure the watchdog exists to prevent. So
        # swallow any failure here: log and continue into the read loop.
        try:
            session.exec(
                update(LiteratureSearch)
                .where(col(LiteratureSearch.id).in_(member_ids[1:]))
                .where(LiteratureSearch.stage == LitStage.FETCHING)  # type: ignore[arg-type]
                .values(stage=LitStage.READING)
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.exception(
                "agent_continue: turn %s failed the cosmetic member READING "
                "flip; continuing (anchor already claimed, watchdog intact)",
                search_id,
            )

    outcome: LoopOutcome | None = None
    try:
        try:
            # Downloads were already reconciled to terminal in Step 1 before the
            # claim, so no reconcile is needed here.
            user_msg = session.exec(
                select(ChatMessage)
                .where(ChatMessage.session_id == chat_session_id)
                .where(ChatMessage.role == ChatRole.USER)
                .order_by(ChatMessage.created_at)  # type: ignore[arg-type]
            ).first()
            abstract_msg = next(
                (
                    m
                    for m in session.exec(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == chat_session_id)
                        .where(ChatMessage.role == ChatRole.ASSISTANT)
                        .order_by(ChatMessage.created_at)  # type: ignore[arg-type]
                    ).all()
                    if (m.message_metadata or {}).get("litsearch_kind") == "abstracts"
                    and (m.message_metadata or {}).get("search_id") == str(search_id)
                ),
                None,
            )

            # Papers across the WHOLE turn (anchor + every followup_of member),
            # DOI-deduped, indexed by creation order — the SAME indexing the
            # read tool uses, so the `idx` values in the listing match what
            # the model passes back.
            raw_papers = session.exec(
                select(LiteraturePaper)
                .where(col(LiteraturePaper.search_id).in_(member_ids))
                .order_by(col(LiteraturePaper.created_at), col(LiteraturePaper.id))
            ).all()
            papers = _dedup_papers(raw_papers)
            ready = [
                (i, p)
                for i, p in enumerate(papers)
                if p.fulltext_status == FulltextStatus.ADDED and p.fulltext_text
            ]

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        search.question if user_msg is None else user_msg.content
                    ),
                },
            ]
            if abstract_msg is not None:
                messages.append({"role": "assistant", "content": abstract_msg.content})

            if not ready:
                # Nothing downloaded successfully (every paper failed/skipped) —
                # there is no full text to read, so don't run a read loop or
                # fabricate a second answer. The Phase-A abstract reply stands;
                # the `finally` watchdog drives the search to DONE.
                logger.warning(
                    "agent_continue: search %s has no readable full text "
                    "(no paper reached fulltext=ADDED) — keeping the abstract "
                    "answer, no read loop",
                    search_id,
                )
                return

            # Step 3: inject the "papers downloaded — read them" turn (the
            # user-facing trigger) and run a READ-ONLY loop. No `litsearch_search`
            # here: a follow-up search would be a NEW user turn, not an autonomous
            # tool that spawns a dangling search and steals the read step.
            # `first_tool_choice` forces the model to read before it can answer.
            listing = "\n".join(f"[{i}] {p.title}" for i, p in ready)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Полные тексты найденных статей скачаны "
                        f"({len(ready)} шт.). Выбери, какие статьи прочитать, и "
                        f"прочитай их инструментом litsearch_read_fulltext "
                        f"(указывай `idx` нужной статьи). Затем дай развёрнутый "
                        f"ответ на русском, опираясь только на прочитанные полные "
                        f"тексты. Если инструмент сообщит, что бюджет чтения "
                        f"исчерпан — прекрати читать и сразу отвечай пользователю. "
                        f"Список статей:\n{listing}"
                    ),
                }
            )
            tools = [litsearch_tools.make_read_fulltext_tool(search_id)]
            outcome = run_loop(
                session,
                chat_session_id,
                messages,
                tools,
                max_iters=settings.LITSEARCH_MAX_ROUNDS * 3,
                first_tool_choice="litsearch_read_fulltext",
                exhausted_system_msg=_READ_EXHAUSTED_MSG,
            )
        except Exception as exc:
            session.rollback()
            logger.exception("agent_continue: search %s failed", search_id)
            search = session.get(LiteratureSearch, search_id)
            if search is not None:
                search.stage = LitStage.FAILED
                search.error = str(exc)
                session.add(search)
                session.commit()
            return

        if outcome.degraded or outcome.final_text is None:
            content = _DEGRADED_TEXT
            meta = {
                "litsearch_kind": "fulltext",
                "search_id": str(search_id),
                "mode_used": "degraded",
            }
        else:
            content = outcome.final_text
            # Tag `fulltext` only when the model actually called the read tool;
            # otherwise the answer is abstract-grounded and labelling it
            # `fulltext` would lie to the panel/telemetry.
            grounded = "litsearch_read_fulltext" in outcome.tool_calls_made
            meta = {
                "litsearch_kind": "fulltext" if grounded else "abstracts",
                "search_id": str(search_id),
            }

        # E1: persisting the generated answer is a DISTINCT failure mode from
        # the loop-execution failures handled above, and deliberately handled
        # differently. A commit failure here means the LLM already produced
        # an answer that never reached the DB — silently swallowing that (as
        # the loop-execution except above does, by design, to keep a single
        # non-raising `agent_continue` contract) would let the Celery task
        # report a clean "succeeded" for a search that has no answer at all,
        # which the panel would then show as a silent DONE. So: log at
        # CRITICAL (this is worse than an ordinary logged-and-swallowed
        # error — it is a lost result), mark the search FAILED here (not just
        # relying on the watchdog `finally`, in case that too can't commit),
        # and re-raise so the exception actually propagates out of
        # `agent_continue` — the Celery task then genuinely fails instead of
        # reporting success for a discarded answer.
        try:
            session.add(
                ChatMessage(
                    session_id=chat_session_id,
                    role=ChatRole.ASSISTANT,
                    content=content,
                    message_metadata=meta,
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.critical(
                "agent_continue: search %s FAILED TO PERSIST the generated "
                "fulltext answer (commit error) — the answer would be "
                "silently discarded while the task still reports success "
                "without this handling; marking FAILED and re-raising",
                search_id,
                exc_info=True,
            )
            failed_search = session.get(LiteratureSearch, search_id)
            if failed_search is not None:
                failed_search.stage = LitStage.FAILED
                failed_search.error = "failed to persist generated answer"
                session.add(failed_search)
                session.commit()
            raise
    finally:
        # Watchdog (spec §2.11): force a terminal stage no matter what, so a
        # worker crash can't strand the search at READING/FETCHING and spin
        # the panel forever. `finally` itself can raise here — e.g. the
        # session's transaction is already stale/aborted from whatever blew
        # up above (or from a failed commit inside the `except` block) — so
        # `rollback()` first to get a clean session, then attempt the
        # terminal-stage write in its own try/except that only logs
        # (`logging.exception`), never re-raises: this `finally` must not
        # itself throw, or the terminal-stage guarantee is void.
        # NOTE: this is still best-effort, not absolute — a hard worker
        # SIGKILL/OOM skips `finally` entirely, which no in-process code can
        # catch. Closing that residual gap needs a periodic sweep for
        # searches stuck non-terminal past a deadline (spec §2.11 mentions
        # this as optional); that sweep is out of scope here, follow-up work.
        # NB: if the `try` above raised (e.g. the E1 persist-failure path),
        # `search`/`failed_search` was already set to FAILED before the
        # raise — this watchdog only ever moves a still-non-terminal search
        # to DONE, so it can never clobber a FAILED that was set above.
        session.rollback()
        try:
            for mid in member_ids:
                settled = session.get(LiteratureSearch, mid)
                if settled is not None and settled.stage not in (
                    LitStage.DONE,
                    LitStage.FAILED,
                ):
                    settled.stage = LitStage.DONE
                    session.add(settled)
            session.commit()
        except Exception:
            logger.exception(
                "agent_continue: turn %s watchdog failed to write terminal "
                "stages",
                search_id,
            )


def _paper_provenance(paper: LiteraturePaper) -> dict[str, str | None]:
    """The provenance payload that would be attached to `experiments.documents`
    once `_ATTACH_PROVENANCE_TO_DOCUMENT` is flipped on (see comment on that
    constant) — the ready-made data source for that future enablement.

    Only surfaces fields that already exist on `LiteraturePaper` today:
    `doi`. `source` is a static tag (not a paper column) identifying the
    ingest channel — every paper reaching this helper came through the
    litsearch/OpenAlex pipeline, so it needs no per-row storage.
    `openalex_id` and `language` are deliberately NOT included: neither
    column exists on `LiteraturePaper` yet, so there is nothing to surface
    for them until those are added too.
    """
    return {
        "source": "litsearch/openalex",
        "doi": paper.doi,
    }


def add_to_database(session: Session, paper_id: uuid.UUID) -> LiteraturePaper:
    """The "Добавить в базу" chat action (task 10): stages the already-fetched
    PDF as a `Document` (L0) and — only when
    `settings.LITSEARCH_INGEST_ENABLED` is true, a coordination gate default
    `False` — enqueues the hard-parse -> graph/ontology ingest pipeline
    (`tasks.enqueue_l1_parse`, mirroring `ingest.run_ingest`).

    Idempotent: a paper whose `document_id` is already set is returned
    unchanged (no new `Document`, no re-enqueue) — a second click on "Добавить
    в базу" is a no-op.

    Concurrency: two concurrent calls for the *same* paper each build their
    own distinct `Document` — that never collides on the `document_id`
    UNIQUE FK, since each `Document.id` is distinct. What must not happen is
    both calls' `UPDATE literature_papers SET document_id=...` landing on the
    same paper row (a lost update: whichever commits last silently overwrites
    the other's `document_id`, orphaning the loser's `Document`/`IngestTask`
    and double-enqueuing `enqueue_l1_parse`). So the `Document` is created
    and `flush()`-ed (to obtain its id) but *not committed*, and the paper is
    "claimed" via a single guarded `UPDATE ... WHERE id=paper_id AND
    document_id IS NULL` (a `rowcount`-checked optimistic-claim guard). Only one concurrent caller's
    UPDATE can match: the loser's `rowcount` is 0, and it rolls back —
    discarding its uncommitted `Document` insert too, so no orphan is ever
    persisted. The winner proceeds to create the `IngestTask` (if the flag is
    on) and commits everything — `Document` + claim + `IngestTask` — in one
    transaction, only enqueuing `tasks.enqueue_l1_parse` *after* that commit
    succeeds, so the worker never observes not-yet-committed rows.
    """
    paper = session.get(LiteraturePaper, paper_id)
    if paper is None:
        raise ValueError(f"LiteraturePaper {paper_id} not found")

    if paper.document_id is not None:
        return paper

    if paper.object_key is None:
        # article-fetcher's `/fetch/sync` requires a DOI (same constraint as
        # `/fetch` used by the async `fetch_async` path) — a paper with
        # neither an `object_key` nor a `doi` is physically unfetchable here.
        resp = (
            litsearch_client.fetch_sync(paper.doi, url=paper.pdf_url)
            if paper.doi is not None
            else None
        )
        object_key = (resp or {}).get("object_key")
        if not isinstance(object_key, str):
            logger.warning(
                "add_to_database: fetch_sync could not retrieve a PDF for "
                "paper %s (doi %s)",
                paper.id,
                paper.doi,
            )
            paper.ingest_status = LitIngestStatus.FAILED
            session.add(paper)
            session.commit()
            session.refresh(paper)
            return paper
        paper.object_key = object_key

    document = Document(
        minio_key=paper.object_key,
        filename=f"{paper.doi or paper.title}.pdf",
        mime_type="application/pdf",
        processing_level=ProcessingLevel.L0,
    )
    session.add(document)
    session.flush()

    if _ATTACH_PROVENANCE_TO_DOCUMENT:
        # Dormant seam — see `_ATTACH_PROVENANCE_TO_DOCUMENT` comment above.
        # Once `experiments.documents` grows source/doi/openalex_id/language
        # columns (schema-owner change, pending OSN sign-off), this is where
        # they'd be populated from the litsearch-owned provenance:
        #     provenance = _paper_provenance(paper)
        #     document.source = provenance["source"]
        #     document.doi = provenance["doi"]
        #     document.openalex_id = provenance.get("openalex_id")
        #     document.language = provenance.get("language")
        #     session.add(document)
        # The columns don't exist yet, so this stays a no-op.
        pass

    claim_result = session.exec(
        update(LiteraturePaper)
        .where(LiteraturePaper.id == paper_id)  # type: ignore[arg-type]
        .where(LiteraturePaper.document_id.is_(None))  # type: ignore[union-attr]
        .values(document_id=document.id)
    )
    if claim_result.rowcount != 1:
        # Lost the race: some other concurrent add_to_database call already
        # claimed this paper between our idempotency check and this UPDATE.
        # Roll back — this discards our uncommitted `Document` insert too,
        # so no orphan is ever persisted — and hand back the winner's row.
        logger.warning(
            "add_to_database: lost claim race for paper %s; another call "
            "already won, rolling back and reloading",
            paper.id,
        )
        session.rollback()
        reloaded = session.get(LiteraturePaper, paper_id)
        assert reloaded is not None
        return reloaded

    paper.document_id = document.id

    task: IngestTask | None = None
    if settings.LITSEARCH_INGEST_ENABLED:
        task = IngestTask(status=IngestStatus.QUEUED, document_ids=[str(document.id)])
        session.add(task)
        session.flush()

        paper.ingest_status = LitIngestStatus.QUEUED
        paper.ingest_task_id = task.id

    session.add(paper)
    session.commit()
    session.refresh(paper)

    if task is not None:
        # Only after commit — so the worker only ever sees committed
        # Document/IngestTask/paper rows.
        tasks.enqueue_l1_parse(task.id, [document.id])

    return paper
