# Litsearch as an Agent Tool — Design Spec (rev. 2)

**Status:** draft for review (2026-07-04, rev. 2 after adversarial grill). Supersedes the
procedural state-machine in `2026-07-03-litsearch-chat-integration-design.md`.

**Author:** litellm-gw (autonomous, under AM cover; OSN asleep).

## 0. Why this rework

The shipped litsearch is a **procedural Celery pipeline** (`start_search → monitor →
synthesize`) that uses the LLM only as a fixed-point text synthesizer and hardcodes
loop control in a prompt-emitted JSON blob (`sufficient`/`followup_query`). OSN's
requirement: **litsearch is a TOOL the model calls in a tool-calling loop** — the model
calls the tool, gets the result, then writes an answer *or another tool call*, and loops
on its own decision. This rework makes the LLM interaction genuinely tool-call-driven
while **reusing the existing machinery that already makes progressive rendering work**.

## 1. Verified constraints (grill-confirmed facts)

- **No tool-calling loop exists today**; `chat.py::answer_message` is a hardcoded
  `if/elif` waterfall. `tool_sdk` is an HTTP tool-*serving* skeleton, not a tool-*calling*
  framework. SPEC_V3 §5.7 is the hypothesis factory, not a loop protocol. The loop must
  be built. SPEC intent (App. C): in-process Python tools; "≥1 tool call/query, degrade
  gracefully."
- **Model supports native tool-calling** (`deepseek/deepseek-v4-flash__or` via
  `llm.autumn-lab.uk` → `finish_reason: tool_calls`, correct args). **Named/forced
  `tool_choice` support is UNVERIFIED — a plan task must verify it before relying on it.**
- **Progressive rendering today works ONLY via async**: the frontend history query has
  **no `refetchInterval`** (`chat.tsx:136-141`); it refetches on POST `onSuccess`
  (`chat.tsx:213`) and when the **litsearch poll** sees `literatureSearch.answers.length`
  grow (`chat.tsx:175-183`, poll `chat.tsx:163-171`, enabled by `effectiveSearchId`). So a
  second answer can only appear progressively if it arrives **after the POST returns**, via
  the litsearch poll observing a new entry in `search.answers`. A synchronous in-request
  loop therefore CANNOT produce the two-answer UX. This dictates the sync/async split below.
- **nginx caps `/api/v1/chat/` at `proxy_read_timeout 300s`** (`nginx.conf:16`),
  `proxy_buffering off`. `fastapi run` adds no per-request timeout. `LITSEARCH_FETCH_TIMEOUT=180`,
  `MAX_ROUNDS=2` (`config.py:117-119`). ⇒ the web request must **not** block on PDF fetch.
- **Stage/answers/panel coupling:** panel + chat poll stop only on `stage∈{done,failed}`
  (`chat.tsx:169`, `LiteraturePanel.tsx:191-192`); today only `synthesize` sets
  `stage=DONE`. Any redesign MUST still drive `stage` to a terminal value and grow
  `search.answers` per answer, or the panel spins forever and the 2nd answer never shows.
- **Side panel is mode-independent**: keyed off `response.literature.search_id` →
  `activeSearchId` (`chat.tsx:210`); each persisted litsearch turn needs
  `message_metadata.search_id` (`routes/litsearch.py:104-109`).

## 2. Architecture — model-driven loop, sync abstract / async fulltext

The design mirrors the *timing* of today's working split (fast synchronous first answer +
background second answer) but replaces both hardcoded synthesis points and the loop
control with **real model tool-calls**.

### 2.1 The generic tool-loop (`agent/loop.py`, new)

```
run_loop(session, chat_session_id, messages, tools, *, max_iters, first_tool_choice=None)
    -> LoopOutcome{ final_text: str|None, tool_calls_made: list[str],
                    literature_search_id: uuid|None, degraded: bool }
```

- Each iteration: `llm.chat(messages, tools=[t.schema...], tool_choice=tc)` where `tc =
  first_tool_choice` on **iteration 0 only**, then `"auto"` (fixes forced-loop bug I1).
- If `resp.tool_calls`: execute each handler `(session, chat_session_id, **args) ->
  dict`; append the assistant tool-call message + a `role:"tool"` result message; continue.
- If `resp.content` and no tool calls: that's the final text → return it.
- Hard stop at `max_iters` (distinct from search "rounds"; concrete cap, see §2.6).
- **Degraded contract:** if `llm.chat` returns a transport/None failure, `run_loop`
  returns `degraded=True, final_text=None` — it NEVER fabricates text. Callers render an
  explicit "LLM unavailable" turn (§2.7), not a template.

### 2.2 `llm.py` changes

- Add `chat(messages, *, tools=None, tool_choice=None, temperature=0.2, metadata=None)
  -> ChatResult{content:str|None, tool_calls:[{id,name,arguments:dict}], ok:bool}`.
  Passes `tools`/`tool_choice`; threads Langfuse `metadata` via `extra_body`
  (`{"metadata": {"session_id": ...}}`) so traces are attributable (§2.8). `ok=False` on
  transport error (explicit failure, no None-as-answer ambiguity).
- **Remove** `synthesize_from_abstracts`, `read_fulltexts`, `complete_json`,
  `_strip_code_fences`. `complete` may remain as a private transport used by `chat`.

### 2.3 litsearch tools (`services/litsearch_tools.py`, new)

- **`litsearch_search(query) -> {search_id, papers:[{idx,title,authors,year,doi,abstract}]}`**
  — creates/ög reuses a `LiteratureSearch` (per round), OpenAlex search via
  `litsearch_client.search`, persists `LiteraturePaper` rows (`_paper_from_openalex`),
  fires background PDF fetch per paper (`fetch_async`), returns compact abstract data +
  `search_id`. Lights the side panel. **Available in both the sync and async phases** (async
  = follow-up rounds).
- **`litsearch_read_fulltext() -> {papers:[{idx,title,doi,text}], pending:int,
  none_available:bool}`** — reconciles fetch jobs to terminal status
  (`job_status`+`_mark_fetched`, moved here / shared helper), returns extracted texts
  (persisted on the paper row so repeat calls don't re-extract — **new `LiteraturePaper.
  fulltext_text` column**), char-capped; `pending` = still downloading; `none_available` =
  OA-only yielded nothing. **Only offered in the async phase** (see §2.4). **Takes NO
  `search_id` argument** — the Phase-B handler is *bound server-side* to the active
  `search_id` (closure), so the model cannot pass a wrong/hallucinated id (grill residual 1).
  The model is instructed: answer with whatever texts are returned; do not re-poll >once.

`add_to_database` stays the UI button action (`POST …/add-to-database`), unchanged.

### 2.4 Two-phase execution (resolves C1/C4/C5)

**Phase A — synchronous, in the web request (fast, hard-bounded well under 300s):**
- Toolset: `litsearch_search` + (AUTO only) `hybrid_search`, `ontology`, `knowledge_graph`.
  **`litsearch_read_fulltext` is deliberately withheld here** — so after `litsearch_search`
  the model has no fulltext tool and thus writes an **abstract-grounded answer** (this is
  how the required "abstract answer first" turn is guaranteed — C5).
- LITERATURE mode: `first_tool_choice = litsearch_search` (primed). AUTO: `tool_choice=auto`.
- `run_loop` with small `max_iters` (default 4) and a wall-clock budget (≈45s). If the
  model called `litsearch_search`, we get `literature_search_id`; the loop's final text is
  the abstract answer. Persist the user row (preserve `chat.py:193` behavior), persist the
  abstract answer as `ChatMessage(metadata={litsearch_kind:"abstracts", search_id})`, and
  **dispatch Phase B** as a Celery task. Return the abstract answer + `literature=
  LiteratureRef(search_id)` immediately.
- If the model did NOT call litsearch (plain AUTO answer), just return the text — normal
  fast chat, no Phase B, no LiteratureSearch created.

**Phase B — background Celery task `litsearch.agent_continue(search_id, chat_session_id)`
(the slow part; no nginx limit):**
- Waits/reconciles PDF fetches (advances `fetch_status` → panel spinners resolve).
- **Re-seeds a fresh message array** `[system, user question, abstract answer]` from the DB
  (the Phase-A tool-call / `role:tool` plumbing is NOT persisted; Phase B does not need it —
  grill residual 1). Binds `litsearch_read_fulltext` to this `search_id` server-side.
- Runs `run_loop` with toolset = `{litsearch_read_fulltext, litsearch_search}`, `max_iters`
  bounded by `MAX_ROUNDS`.
  The model calls `read_fulltext` → writes a **fulltext-grounded answer** → optionally
  calls `litsearch_search` again (a follow-up round — this is "can loop", model-decided) →
  answers again → finishes.
- Each text turn is persisted as `ChatMessage(metadata={litsearch_kind:"fulltext",
  search_id})` **and appended to `search.answers`** so the frontend litsearch poll
  (`answers.length`) invalidates history and renders it progressively (the mechanism that
  actually works, §1).
- On completion set `stage=DONE`; on unrecoverable failure set `stage=FAILED` and persist
  an explicit degraded turn. This re-homes the terminal stage the old `synthesize` set (C3).

### 2.5 Modes / tabs

- **AUTO:** Phase A toolset includes `litsearch_search` alongside the other tools — the
  model may choose litsearch. This is how litsearch appears in the **Auto** tab (today's
  gap). If chosen, Phase B runs; `ChatMessageResponse.literature` is set so the panel
  lights up in AUTO too (fixes I5).
- **LITERATURE:** Phase A primes `litsearch_search` (forced turn 0), else identical. Keeps
  the dedicated tab's "always search" contract.
- **ONTOLOGY / KNOWLEDGE_GRAPH:** unchanged legacy branches; out of scope. They coexist
  with the loop. (Folding them into the loop is future work.)

### 2.6 Loop safety (resolves I1/I2)

- `first_tool_choice` applies to **iteration 0 only**; all later iterations use `"auto"`.
- `max_iters` is a **distinct hard cap** (Phase A: 4; Phase B: 6), separate from
  `MAX_ROUNDS` (follow-up searches). Reaching `max_iters` ⇒ force a final no-tools answer
  turn (`tool_choice="none"`); if still no text ⇒ degraded turn.
- `read_fulltext` may be called at most twice per search (guard in the handler); the model
  is instructed to answer from available texts and not busy-poll `pending`.

### 2.7 Fail-loud (resolves I3)

- **Remove** `_template_answer` (`litsearch.py:106`) and `_NO_FULLTEXT_ANSWER`
  (`litsearch.py:55`) — the two template-masquerade sources — along with the §2.2 list.
- On `degraded` (LLM unreachable / no text), the persisted turn is an explicit
  user-visible "LLM недоступен — ответ не сформирован" status with `mode_used="degraded"`;
  never a fabricated grounded answer. `chat().ok=False` is the single failure signal.
- **`GET /api/v1/utils/llm-health`** (new): performs a real minimal gateway round-trip
  (not just env presence) so a misconfigured/unreachable LLM is visible, not silent.

### 2.8 Observability (acceptance)

- Committed config (not commented-out): `LLM_BASE_URL=https://llm.autumn-lab.uk/v1`,
  `LLM_MODEL=deepseek/deepseek-v4-flash__or`. Every `llm.chat` call threads
  `metadata.session_id`. Acceptance: loop LLM calls + tool turns appear in **Langfuse
  (langfuse.autumn-lab.uk)** and the **LiteLLM proxy (llm.autumn-lab.uk)** logs.

### 2.9 Fetch tiers config (OSN override 2026-07-04, msg 1880)

- article-fetcher: **piracy tiers stay ON** — `scidb_enabled=True`, `scihub_mirrors`
  populated (as committed). OSN chose coverage over the compliance/flakiness concern; his
  call. Env-togglable (`SCIDB_ENABLED`, `SCIHUB_MIRRORS`). **No change to article-fetcher
  defaults** — Task 1 (OA-only) is VOIDED/reverted. `read_fulltext` still handles
  `none_available` gracefully for the papers with no obtainable PDF.

### 2.10 Kept vs removed (grill-corrected)

**Kept/reused:** `litsearch_client.py`; `models/litsearch.py` tables (+ new
`fulltext_text` column) and `round`/`followup_of` chain; `schemas/litsearch.py`;
`routes/litsearch.py` poll + add-to-database; `_paper_from_openalex`; the fetch job +
reconcile *logic* (moved into `read_fulltext`/Phase B); `add_to_database`.

**Removed:** `litsearch.py::start_search` synchronous synthesis; the standalone
`litsearch.monitor` **and** `litsearch.synthesize` tasks + `synthesize_task` re-enqueue
(replaced by the single `agent_continue` task — resolves C2, no orphaned dispatch);
`try_begin_reading`/`revert_to_fetching`/READING-stage machinery; `_literature_answer`
shim in `chat.py`; `llm.synthesize_from_abstracts`/`read_fulltexts`/`complete_json`/
`_strip_code_fences`; `_template_answer`/`_NO_FULLTEXT_ANSWER`.

### 2.11 Hardening (grill residuals 2–4)

- **Terminal-stage watchdog:** `agent_continue` wraps its body in `try/finally` that ALWAYS
  writes a terminal `stage` (`DONE`, or `FAILED` on exception) — so a worker crash/OOM can't
  strand the search at `FETCHING` and spin the panel forever. Optionally a periodic sweep for
  searches stuck non-terminal past a deadline.
- **Per-session note:** the old `synthesize` held a per-session Redis lock; `agent_continue`
  operates on a distinct `search_id` per question so contention is low — no lock required, but
  the `add_to_database` optimistic-claim guard remains untouched.
- **Migration:** the new `LiteraturePaper.fulltext_text` column is an explicit Alembic
  migration task in the plan (chain after current head).

## 3. Out of scope
Token streaming; reworking ontology/KG modes; on-box gateway redeploy; the вики tab.

## 4. Testing strategy
- **Unit (respx-mocked gateway):** `run_loop` executes tool calls, appends `role:tool`,
  terminates on final content, flips `tool_choice` to auto after iter 0, honors `max_iters`
  (forces final answer), returns `degraded` on transport failure (no fabricated text).
  Each handler: `litsearch_search` persists rows + fires fetch + returns search_id;
  `read_fulltext` bounds, persists text, returns `pending`/`none_available`, capped at 2
  calls. Phase-A withholds read_fulltext ⇒ abstract turn. Phase-B sets `stage=DONE`.
- **Integration (live gateway, real model):** Phase A returns a real non-template abstract
  answer with `tool_calls` actually emitted + panel rows created; Phase B produces a real
  fulltext answer appended to `search.answers` and `stage=DONE`.
- **e2e (Playwright MCP):** browser, Auto AND Литература tabs: abstract answer → panel
  spinners → fulltext answer appears progressively → add-to-DB → L1; verify traces in
  Langfuse + LiteLLM proxy; verify a forced-unreachable LLM shows the degraded turn (not a
  template).

## 5. Acceptance criteria
1. Литература AND Auto: literature question ⇒ **real LLM** abstract answer (Phase A) then a
   **real LLM** fulltext answer (Phase B), driven by model `litsearch_search` /
   `litsearch_read_fulltext` tool calls (verified non-template).
2. Side panel lights up and reaches a terminal `done`/`failed` (no infinite poll),
   regardless of tab; add-to-DB → L1.
3. Model can loop (a second `litsearch_search`) on its own decision.
4. LLM unreachable ⇒ explicit degraded turn, **never** a template masquerading as grounded.
5. All LLM/tool calls visible in Langfuse + LiteLLM proxy.
6. No `synthesize_from_abstracts`/`read_fulltexts`/`complete_json`/`_template_answer`/
   `_NO_FULLTEXT_ANSWER`/prompt-JSON remains; no orphaned `monitor`/`synthesize` dispatch.
7. Piracy download tiers OFF by default, env-togglable.
