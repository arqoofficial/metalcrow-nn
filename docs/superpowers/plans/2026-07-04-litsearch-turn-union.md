# Litsearch Turn-Union ("accumulate") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Replace the current "bind the turn to the LAST literature search" behavior with a per-turn UNION: read + answer from every search the model made this turn (deduped by DOI), show every query, and count only *successful* searches toward the search cap.

**Architecture:** A chat turn's searches are grouped by reusing the currently-dead `LiteratureSearch.followup_of` column as a turn-group key. The FIRST search created in the turn is the *anchor*; searches 2..N get `followup_of = anchor.id`. All downstream binding stays keyed to the anchor id, so the Phase-B dispatch signature AND the frontend fetch contract (`GET /litsearch/{anchor}`) are UNCHANGED — the route aggregates the union server-side. Phase B reads the DOI-deduped union of the turn's papers and writes ONE grounded answer.

**Tech Stack:** FastAPI + SQLModel + Celery (litsearch queue) backend; React/TanStack Query frontend. No DB migration (reuses existing `followup_of`).

## Global Constraints

- **No DB migration.** Reuse `LiteratureSearch.followup_of` (uuid|None, currently never written non-None) as the turn-group key. Do NOT touch `followup_search_id` (that one is surfaced in the API and means something else).
- **Anchor = first search id** in `LoopOutcome.literature_search_ids`. Members = every other search id created in the same turn.
- **Preserve Phase-B idempotency invariants:** the atomic `FETCHING→READING` claim, the heartbeat re-enqueue, and the `finally` watchdog must keep exactly-once semantics. The anchor's stage is the turn lock.
- **Truthful tagging:** an answer is tagged `fulltext` only when the read tool was actually called (`"litsearch_read_fulltext" in outcome.tool_calls_made`), else `abstracts`. Keep this.
- **Dedup by DOI**, keeping the first occurrence in `(created_at, id)` order. Papers with `doi is None` are NOT deduped against each other (each kept).
- All user-facing strings stay Russian, matching surrounding code.
- Never run pytest against the live `app` DB (the `db` fixture wipes all Users). Use the scratch DB per the repo test convention.

---

### Task 1: Loop collects all search ids + success/attempt caps

**Files:**
- Modify: `backend/app/services/agent/loop.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/app/tests/services/agent/test_agent_loop.py` (adjust existing)

**Interfaces:**
- Produces: `LoopOutcome.literature_search_ids: list[uuid.UUID]` (every search that returned a `search_id`, in call order, deduped). `LoopOutcome.literature_search_id` stays as a back-compat read-only alias returning `literature_search_ids[0] if literature_search_ids else None`.
- Consumes (new `run_loop` kwargs): `max_successful_searches: int | None = None` (cap on searches that returned ≥1 paper), `max_tool_calls: int | None` (existing; now the *attempts* ceiling).

**Design:** A "successful" search = tool_result dict has a non-empty `papers` list. Track `successful_searches` separately from `tool_calls_used`. Force the abstract-only answer when EITHER `tool_calls_used >= max_tool_calls` OR `successful_searches >= max_successful_searches`.

- [ ] **Step 1: Update `LoopOutcome`** — replace the scalar field with a list + alias.

```python
@dataclass
class LoopOutcome:
    final_text: str | None = None
    tool_calls_made: list[str] = field(default_factory=list)
    literature_search_ids: list[uuid.UUID] = field(default_factory=list)
    degraded: bool = False

    @property
    def literature_search_id(self) -> uuid.UUID | None:
        """Back-compat: the anchor (first) search of the turn, or None."""
        return self.literature_search_ids[0] if self.literature_search_ids else None
```

- [ ] **Step 2: Add the success cap param + counter** to `run_loop`. Add `max_successful_searches: int | None = None` to the signature. Initialize `successful_searches = 0` beside `tool_calls_used = 0`.

- [ ] **Step 3: Widen the cap gate** (the block at the top of the loop that currently reads `if max_tool_calls is not None and tool_calls_used >= max_tool_calls:`):

```python
        cap_hit = (
            max_tool_calls is not None and tool_calls_used >= max_tool_calls
        ) or (
            max_successful_searches is not None
            and successful_searches >= max_successful_searches
        )
        if cap_hit:
            forced_text = _forced_answer(
                messages, schemas, metadata, exhausted_system_msg
            )
            if forced_text is not None:
                outcome.final_text = forced_text
                outcome.degraded = False
            else:
                outcome.final_text = None
                outcome.degraded = True
            return outcome
```

- [ ] **Step 4: Record every search + count successes** — replace the `if isinstance(tool_result, dict) and "search_id" in tool_result:` block:

```python
            tool_calls_used += 1
            if isinstance(tool_result, dict) and "search_id" in tool_result:
                sid = uuid.UUID(str(tool_result["search_id"]))
                if sid not in outcome.literature_search_ids:
                    outcome.literature_search_ids.append(sid)
                if tool_result.get("papers"):
                    successful_searches += 1
```

- [ ] **Step 5: Config** — in `backend/app/core/config.py`, next to `LITSEARCH_MAX_SEARCHES`, add:

```python
    # Cap on *successful* (>=1 paper) literature searches per chat turn. A
    # larger LITSEARCH_MAX_SEARCH_ATTEMPTS bounds total attempts so a run of
    # empty/failed queries can't loop forever while still allowing retries.
    LITSEARCH_MAX_SEARCH_ATTEMPTS: int = 6
```
(`LITSEARCH_MAX_SEARCHES` stays the successful cap, default 3.)

- [ ] **Step 6: Tests** — update `test_agent_loop.py`: any assertion on `outcome.literature_search_id` still works via the alias; add a test that two search tool-results yield `literature_search_ids == [id1, id2]`, and a test that `max_successful_searches` forces an answer after N papered searches while empty searches don't count. Run: `pytest backend/app/tests/services/agent/test_agent_loop.py -v`.

- [ ] **Step 7: Commit** — `git add` the three files; `git commit -m "feat(litsearch): loop records all turn search ids + success/attempt caps"`.

---

### Task 2: Phase A groups the turn + dispatches the anchor

**Files:**
- Modify: `backend/app/services/chat.py` (`_run_litsearch_phase_a`)
- Test: `backend/app/tests/services/test_chat_litsearch.py` (or wherever Phase-A tests live; adjust existing)

**Interfaces:**
- Consumes: `outcome.literature_search_ids` (Task 1), `settings.LITSEARCH_MAX_SEARCH_ATTEMPTS`.
- Produces: sibling searches grouped by `followup_of = anchor`; `LiteratureRef(search_id=anchor, paper_count=<deduped union count>)` — shape UNCHANGED.

- [ ] **Step 1: Pass both caps to `run_loop`** — in the `run_loop(...)` call, change `max_tool_calls=settings.LITSEARCH_MAX_SEARCHES` to:

```python
        max_tool_calls=settings.LITSEARCH_MAX_SEARCH_ATTEMPTS,
        max_successful_searches=settings.LITSEARCH_MAX_SEARCHES,
```

- [ ] **Step 2: Guard on the list** — replace `if outcome.literature_search_id is None:` with `if not outcome.literature_search_ids:` (the degraded/AUTO-fallthrough branch is otherwise unchanged; it may keep reading `outcome.literature_search_id`, which is still valid via the alias).

- [ ] **Step 3: Compute anchor + group members** — after the degraded-answer guard, where `search_id = outcome.literature_search_id` is set, replace with:

```python
    search_ids = outcome.literature_search_ids
    anchor_id = search_ids[0]
    # Group this turn's searches under the anchor (reuses the otherwise-dead
    # followup_of as a turn-group key) so Phase B and the panel route can
    # aggregate the union without a new column.
    member_ids = search_ids[1:]
    if member_ids:
        session.exec(
            update(LiteratureSearch)
            .where(col(LiteratureSearch.id).in_(member_ids))
            .values(followup_of=anchor_id)
        )
        session.commit()
    search_id = anchor_id
```
(Ensure `update`, `col`, `LiteratureSearch` are imported in chat.py; add imports if missing.)

- [ ] **Step 4: Deduped union paper_count** — replace the `paper_count = len(...)` block:

```python
    union_papers = session.exec(
        select(LiteraturePaper).where(col(LiteraturePaper.search_id).in_(search_ids))
    ).all()
    seen_dois: set[str] = set()
    paper_count = 0
    for p in union_papers:
        if p.doi is None:
            paper_count += 1
        elif p.doi not in seen_dois:
            seen_dois.add(p.doi)
            paper_count += 1
```

- [ ] **Step 5:** Leave the abstract `ChatMessage` (tagged `search_id=str(anchor_id)`), `_dispatch_agent_continue(session, anchor_id, chat_session_id)`, and `LiteratureRef(search_id=anchor_id, paper_count=paper_count)` as-is (anchor id already substituted for `search_id`).

- [ ] **Step 6: Tests** — add a Phase-A test: model makes 2 searches → both rows share `followup_of == anchor` (anchor's own `followup_of` stays None), `LiteratureRef.paper_count` equals the DOI-deduped union, dispatch called once with the anchor id. Run the Phase-A test module.

- [ ] **Step 7: Commit** — `git commit -m "feat(litsearch): Phase A groups turn searches under anchor, dedups union count"`.

---

### Task 3: Phase B reads the DOI-deduped union

**Files:**
- Modify: `backend/app/services/litsearch.py` (`agent_continue`)
- Modify: `backend/app/services/litsearch_tools.py` (`make_read_fulltext_tool`)
- Test: `backend/app/tests/services/test_litsearch_agent_continue.py`, `test_litsearch_tools_fulltext.py`

**Interfaces:**
- `agent_continue(session, search_id, chat_session_id)` — signature UNCHANGED; `search_id` is the anchor. It derives `member_ids` internally.
- `make_read_fulltext_tool(search_id)` — signature UNCHANGED; internally reads the union of the anchor + its `followup_of` members.

- [ ] **Step 1: Helper to resolve turn members** — add near the top of `litsearch.py`:

```python
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
```

- [ ] **Step 2: Reconcile ALL members in the heartbeat wait** — in `agent_continue` Step 1, resolve `member_ids = _turn_search_ids(session, search_id)` before the `if search.stage == LitStage.FETCHING:` block, then reconcile every member and AND the results:

```python
    member_ids = _turn_search_ids(session, search_id)
    if search.stage == LitStage.FETCHING:
        all_terminal = all(
            reconcile(session, mid, now_ts=now, deadline_ts=deadline_ts)
            for mid in member_ids
        )
        if not all_terminal and now < deadline_ts:
            if _reenqueue_heartbeat(session, search_id, chat_session_id):
                logger.info(
                    "agent_continue: turn %s still downloading — heartbeat "
                    "re-enqueued in %ss",
                    search_id,
                    settings.LITSEARCH_HEARTBEAT_SECONDS,
                )
            return
```
(`all(...)` short-circuits, so reconcile every member explicitly first if reconcile has side effects that must run on all — use a list comprehension materialized before `all()`: `results = [reconcile(...) for mid in member_ids]; all_terminal = all(results)`.)

- [ ] **Step 3: Claim stays on the anchor** — the `update(LiteratureSearch).where(id==search_id).where(stage==FETCHING).values(stage=READING)` block is unchanged (anchor = turn lock). After a successful claim, also flip members to READING best-effort so the panel reflects it:

```python
    if member_ids[1:]:
        session.exec(
            update(LiteratureSearch)
            .where(col(LiteratureSearch.id).in_(member_ids[1:]))
            .where(LiteratureSearch.stage == LitStage.FETCHING)
            .values(stage=LitStage.READING)
        )
        session.commit()
```

- [ ] **Step 4: Gather the DOI-deduped union** — replace the `papers = session.exec(...where search_id == search_id...)` block:

```python
            raw_papers = session.exec(
                select(LiteraturePaper)
                .where(col(LiteraturePaper.search_id).in_(member_ids))
                .order_by(col(LiteraturePaper.created_at), col(LiteraturePaper.id))
            ).all()
            papers = _dedup_by_doi(raw_papers)
            ready = [
                (i, p)
                for i, p in enumerate(papers)
                if p.fulltext_status == FulltextStatus.ADDED and p.fulltext_text
            ]
```
Add a module-level helper:
```python
def _dedup_by_doi(papers: list[LiteraturePaper]) -> list[LiteraturePaper]:
    """Keep first occurrence per DOI (in the given order); papers with no DOI
    are all kept. Preserves ordering so `idx` is stable across Phase B and the
    read tool."""
    seen: set[str] = set()
    out: list[LiteraturePaper] = []
    for p in papers:
        if p.doi is None:
            out.append(p)
        elif p.doi not in seen:
            seen.add(p.doi)
            out.append(p)
    return out
```

- [ ] **Step 5: Answer stays tagged with the anchor** — the persisted `ChatMessage` metadata keeps `"search_id": str(search_id)` (anchor). No change needed there.

- [ ] **Step 6: Watchdog drives ALL members terminal** — in the `finally`, after `session.rollback()`, drive every member (not just the anchor) to DONE if still non-terminal:

```python
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
                "agent_continue: turn %s watchdog failed to write terminal stages",
                search_id,
            )
```
(`member_ids` is in scope from Step 2; if an early `return` happens before it's set, that path doesn't reach this `finally` body meaningfully — guard with `member_ids = locals().get("member_ids", [search_id])` only if needed, else ensure `member_ids` is computed before the `try`.)

- [ ] **Step 7: Read tool reads the union** — in `litsearch_tools.py::make_read_fulltext_tool`, change the query to read the anchor's whole turn and dedup by DOI. Add the same `_turn_search_ids` + `_dedup_by_doi` logic (import from `litsearch`, or inline). The handler's paper list becomes the deduped union ordered by `(created_at, id)`; `idx` maps into that list exactly as the listing injected by `agent_continue`.

- [ ] **Step 8: Tests** — update/extend: (a) `agent_continue` with two grouped searches reads papers from BOTH; (b) a duplicate DOI across two searches appears once in `ready`/listing; (c) the read tool returns the union and respects `idx` over the deduped order; (d) watchdog drives all members to DONE. Run both test modules against the scratch DB.

- [ ] **Step 9: Commit** — `git commit -m "feat(litsearch): Phase B reads DOI-deduped union of the turn's searches"`.

---

### Task 4: Panel route + frontend show the union and every query

**Files:**
- Modify: `backend/app/api/routes/litsearch.py` (`get_search`)
- Modify: `backend/app/schemas/litsearch.py` (`LiteratureSearchPublic`)
- Modify: `frontend/src/lib/litsearch.ts` (type), `frontend/src/components/Chat/LiteraturePanel.tsx` (render)
- Test: `backend/app/tests/api/routes/test_litsearch.py` (adjust existing)

**Interfaces:**
- `GET /litsearch/{anchor}` now returns the union of the turn's papers (deduped by DOI) + `queries: list[str]` (every search's question, anchor first).

- [ ] **Step 1: Schema** — add to `LiteratureSearchPublic`: `queries: list[str] = Field(default_factory=list)`.

- [ ] **Step 2: Route union** — in `get_search`, resolve the turn members (anchor = `search.id`; members = searches with `followup_of == search.id`), gather papers across all of them, dedup by DOI (first-occurrence), and build `queries` = `[anchor.question] + [member.question ...]` in `(created_at, id)` order. Widen the `answers` filter to match any member's `search_id`. Return `queries` in the response. (Reuse `col(LiteraturePaper.search_id).in_(member_ids)`; the ingest-status override loop stays the same, just over the union list.)

- [ ] **Step 3: Frontend type** — in `litsearch.ts`, add `queries: string[]` to `LiteratureSearchPublic`.

- [ ] **Step 4: Frontend render** — in `LiteraturePanel.tsx`, when `data.queries.length > 0`, render them (e.g. a small list of Badges/`CardDescription` lines under the title: "Запросы: …") so the user sees every search that ran this turn.

- [ ] **Step 5: Tests** — route test: an anchor with one grouped member returns the deduped union of papers, `queries` lists both questions, and answers from both members are included. Run the route test module.

- [ ] **Step 6: Commit** — `git commit -m "feat(litsearch): panel route + UI show turn union of papers and all queries"`.

---

## Self-Review notes
- Spec coverage: "don't bind to last search" → Tasks 2/3 (anchor-group union). "log & show all litsearches" → Task 4 `queries`. "accumulate / union deduped by DOI" → Tasks 2/3/4. "count only successful, larger cap for unsuccessful" → Task 1.
- Type consistency: `_turn_search_ids`/`_dedup_by_doi` defined in `litsearch.py`, reused by the read tool and route (import or mirror). `LoopOutcome.literature_search_id` alias keeps existing call sites valid.
- No migration; `followup_of` repurposed (verified dead today).
