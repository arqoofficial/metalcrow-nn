# Вики Tab — Design Spec

**Status:** draft for review (2026-07-04). Sibling feature to
`2026-07-04-litsearch-as-agent-tool-design.md`; reuses its machinery
(`agent/loop.py::run_loop`, `llm.chat`) verbatim — this spec adds no new loop
mechanics, only a new tool pair and a new mode branch.

**Author:** litellm-gw (autonomous, under AM cover).

## 0. Why this feature

Metallurgists need grounded answers from a **local corpus of 562 Docling-parsed
Markdown documents** (Доклады/Журналы/Статьи/etc., YAML frontmatter + body,
provided by AM via Yandex disk, task #23) that lives **outside** the app's own
ingest pipeline. The existing `Литература` tab searches the *open web*
(OpenAlex); `Онтология`/`Граф знаний` answer from *structured, already-ingested*
data. None of the three can answer "what does our own scanned corpus say about
X" without a human first ingesting every file through `svc-parse-docling` →
`Document`/L1. **Вики** closes that gap: the model searches and reads the raw
corpus directly, in the same native tool-calling loop already built for
litsearch, and answers with file-path citations. No ingest, no `Document` rows,
no ontology/graph writes — a read-only grounding source.

## 0.1 Naming collision — read this first

**This is NOT the existing `Wiki` page.** The sidebar already has a top-level
`Wiki` page (`frontend/src/routes/_layout/wiki.tsx`, English label "Wiki",
path `/wiki`) backed by `app/services/wiki.py` / `app/schemas/wiki.py` /
`app/api/routes/wiki.py`. That feature is an **entity/document browser**: it
lists `Document` rows that have been hard-ingested to L1 and shows their
parsed OKF markdown (`Document.okf_raw_path`, read via
`app/services/okf.py::read_okf_markdown`, rooted at the **existing**
`settings.OKF_ROOT` setting), plus Material/Property/etc. wiki pages backed by
Postgres. It is fully unrelated to this feature, must not be touched, and must
not share its config knob:

- **Different corpus, different semantics.** `OKF_ROOT` is scoped to files this
  app's own pipeline parsed and linked to a `Document` row
  (`okf_raw_path` FK-adjacent). The new corpus (task #23's 562-file AM-provided
  set) has **no** `Document` rows and never will — reusing `OKF_ROOT` for it
  would silently conflate "files our pipeline ingested" with "files a human
  dropped in a folder," which is exactly the kind of ambiguity that causes a
  future maintainer to point the wrong reader at the wrong root. **New,
  distinct setting: `WIKI_CORPUS_ROOT`.**
- **Different module names.** New handler module is `wiki_tools.py` (mirrors
  `litsearch_tools.py`'s naming/location convention: top-level
  `app/services/`, not inside `app/services/agent/`, which holds only the
  generic loop). The existing `app/services/wiki.py` is untouched.
- **Different UI surface.** The new "Вики" affordance is a **mode tab inside
  the Chat page** (`chat.tsx` `modeOptions`, alongside Авто/Онтология/Граф
  знаний/Литература), not a new sidebar route. The existing sidebar "Wiki"
  page is untouched. The mode's hint text must make the distinction legible to
  users (see §2.5) since both are reachable from the same app and both are
  named "wiki"/"Вики" in Russian UI copy.

## 1. Verified constraints (read-first facts)

- `agent/loop.py::run_loop(session, chat_session_id, messages, tools, *,
  max_iters, first_tool_choice=None) -> LoopOutcome{final_text, tool_calls_made,
  literature_search_id, degraded}` already exists (litsearch rework, commit
  `97ca521`). It is generic: nothing in it is litsearch-specific except the
  `literature_search_id` extraction (keyed on a `"search_id"` key in a tool's
  return dict — wiki tools **must not** return a `search_id` key, or
  `run_loop` will try `uuid.UUID(str(...))` on whatever they put there and
  raise). Wiki tool results carry no such key — verified safe.
- `llm.chat(messages, *, tools=None, tool_choice=None, temperature=0.2,
  metadata=None) -> ChatResult{content, tool_calls, ok}` is the single gateway
  transport; `ok=False` on any transport/parse failure or empty
  `LLM_BASE_URL`, **never** fabricates text. Reused as-is.
- `chat.py::answer_message` branches on `request.metadata.mode` (`ChatMode`)
  with early-return branches for `LITERATURE` (own function,
  `_run_litsearch_phase_a`) ahead of the `AUTO`/`is_gap_click`/waterfall logic.
  **Вики follows the same shape**: an early-return dedicated branch, not folded
  into the `AUTO` waterfall (unlike litsearch, which IS offered inside `AUTO`).
  Rationale: task framing ("a Вики mode/tab alongside Авто/...") treats it as a
  peer explicit mode the user opts into, same as Онтология/Граф знаний today,
  which also do not intrude on `AUTO`.
- `ChatMode` (`app/schemas/chat.py`) is a `StrEnum`: `AUTO, ONTOLOGY,
  KNOWLEDGE_GRAPH, LITERATURE`. Frontend `modeOptions`
  (`frontend/src/routes/_layout/chat.tsx:33-54`) is a hand-maintained array of
  `{value, label, hint}` — **not** auto-derived from the enum. The TS union
  type `ChatMode` in `frontend/src/client/types.gen.ts:44` is generated from
  the backend OpenAPI schema via `bash scripts/generate-client.sh` (runs
  `app.main.app.openapi()` → `openapi.json` → `bun run generate-client` →
  `bun run lint`) — **must be re-run** after adding `ChatMode.WIKI`, or the
  frontend type check fails on the new `mode: "wiki"` literal.
- `ChatMessageResponse.mode_used: str` is already untyped (`str`, not the
  enum) — a new `"wiki"` value needs no backend schema change beyond the
  `ChatMode` enum member. Frontend `modeUsedLabel`/`modeUsedVariant`
  (`chat.tsx:56-71`) are `Record<ChatModeUsed, ...>` where `ChatModeUsed` is a
  hand-written union in `frontend/src/lib/postChatMessage.ts` — must add
  `"wiki"` there too or the badge falls back to the raw string via
  `ModeUsedBadge`'s `known ? ... : mode` fallback (not broken, just unstyled).
- The existing "Agent response" card in `chat.tsx:434-464` renders whenever
  `sendMessageMutation.data.mode_used !== "literature"` — Вики's response
  (claims + summary) renders through this **existing** card with no new
  component, same as Онтология/Граф знаний today, as long as `chat.py` returns
  a `ChatMessageResponse` with `claims: [Claim(...)]` (not the literature-style
  no-card response).
- Existing containment idiom for corpus-root escape checks is already in the
  codebase (`app/services/okf.py::read_okf_markdown`, lines 16-26):
  `full_path = (root.resolve() / relative_path).resolve()`, then
  `full_path.relative_to(root)` inside `try/except ValueError`. This is
  functionally equivalent to an `os.path.realpath` containment check and is
  the established local idiom — the new `read_okf` tool reuses this exact
  pattern (not a hand-rolled string-prefix check, which is the classic
  `/app/okf_wiki` vs `/app/okf_wiki_evil` prefix-match bug).
- `compose.override.yml` is a **committed**, auto-loaded local-dev overlay
  (distinct from the **gitignored** `compose.local.yml`) already used for
  live-reload volume mounts and host port bindings. It is the natural home for
  a host-specific corpus bind mount (see §2.2) — but it currently contains no
  hardcoded absolute host paths (everything is `${VAR:-default}`), a pattern
  the new mount must not break.
- Corpus: `/home/claude/a2a-shared/nornickel-cleaned-corpus/extracted/RAW_DATA/`,
  562 files, subdirs `Доклады/Журналы/Статьи/Материалы конференций/Обзоры/`
  (verified via `find | wc -l`). Each file: YAML frontmatter (`---...---`) +
  Markdown body. Small enough that pure-Python scanning (no ripgrep binary
  dependency) comfortably completes within the loop's per-call budget.

## 2. Architecture

### 2.1 Two new tools, one new mode branch, zero new loop mechanics

```
ChatMode.WIKI = "wiki"          (app/schemas/chat.py)
wiki_tools.make_grep_tool()      -> Tool(name="grep_okf", ...)
wiki_tools.make_read_tool()      -> Tool(name="read_okf", ...)
chat.py::_run_wiki_mode(session, chat_session_id, request) -> ChatMessageResponse
    = run_loop(..., tools=[grep_tool, read_tool], max_iters=WIKI_MAX_ITERS,
               first_tool_choice="grep_okf")
```

`_run_wiki_mode` is a **single synchronous call**, unlike litsearch's Phase
A/Phase B split — no Celery dispatch, no side panel, no background reconcile.
The corpus is local filesystem reads with no network I/O and no long-running
external job to wait on, so there is nothing for a Phase B to do. This directly
satisfies "Wiki is simpler: ONE synchronous loop, no Phase B, no side panel."

`first_tool_choice="grep_okf"` primes iteration 0 (same mechanism `LITERATURE`
uses for `litsearch_search`) so the model is forced to ground itself in the
corpus before it can answer from parametric knowledge alone. All later
iterations get `tool_choice="auto"` (this flip is `run_loop`'s existing
behavior — nothing to build).

### 2.2 Config: `WIKI_CORPUS_ROOT` + mount (design decision)

**New settings** (`app/core/config.py`, alongside the `LITSEARCH_*` block):

```python
# Вики tab — read-only local corpus of pre-parsed OKF markdown (task #23),
# searched/read by the model via grep_okf/read_okf in the agent loop. Distinct
# from OKF_ROOT (app/services/okf.py) — that root is scoped to files THIS
# app's own ingest pipeline parsed and linked to a Document row; this corpus
# has no Document rows and is a separate, human-curated drop of 562 files.
WIKI_CORPUS_ROOT: Path = Path("/app/okf_wiki")
WIKI_GREP_MAX_FILES: int = 2000       # hard cap on files scanned per grep_okf call
WIKI_GREP_MAX_RESULTS: int = 20       # default + clamp ceiling for max_results arg
WIKI_GREP_SNIPPET_CHARS: int = 200    # per-match snippet char cap
WIKI_READ_MAX_LINES: int = 400        # default + clamp ceiling for read_okf limit arg
WIKI_READ_CHAR_CAP: int = 20000       # hard cap on bytes returned per read_okf call
WIKI_MAX_ITERS: int = 6               # run_loop max_iters for wiki mode
```

**Mount split (mirrors the repo's existing `LLM_BASE_URL`/host-specific-value
convention — env var with a portable default committed, real value supplied
per-environment):**

- `compose.yml` (committed, portable, no host-specific paths): backend service
  gets `WIKI_CORPUS_ROOT=${WIKI_CORPUS_ROOT:-/app/okf_wiki}` in `environment:`.
  **No bind mount added here** — `compose.yml` must build/run correctly on any
  machine with zero host corpus present (mirrors how `OKF_ROOT`'s own mount,
  `./okf:/app/okf`, is a *repo-relative* directory, never a host-absolute
  path). If `WIKI_CORPUS_ROOT` points at a directory that doesn't exist, the
  tools degrade to "corpus empty" (see §2.3), never a crash.
- `compose.override.yml` (committed, local-dev overlay, already the
  established home for this-box dev conveniences): add a **new env-var
  indirection**, not a hardcoded absolute path, to keep the file portable —
  ```yaml
    backend:
      volumes:
        - ./backend/app:/app/backend/app   # (existing line, unchanged)
        - ${WIKI_CORPUS_HOST_PATH:-./okf}:/app/okf_wiki:ro
  ```
  `WIKI_CORPUS_HOST_PATH` defaults to the repo-relative `./okf` (empty on a
  fresh checkout → empty corpus, not an error) and is overridden **in `.env`**
  (untracked, host-local, same file that already carries secrets per this
  box's convention) to the real path:
  `WIKI_CORPUS_HOST_PATH=/home/claude/a2a-shared/nornickel-cleaned-corpus/extracted/RAW_DATA`.
  This keeps `compose.override.yml` itself free of box-specific absolute
  paths — consistent with every other host-specific value in that file
  (`${POSTGRES_PORT:-5432}` etc.) — while making the real 562-doc corpus
  available on this box with one `.env` line.
- `.env.example`: document `WIKI_CORPUS_ROOT=/app/okf_wiki` (committed
  default, matches `compose.yml`) and a **commented** line showing the
  override shape: `# WIKI_CORPUS_HOST_PATH=/path/to/parsed/okf/corpus`.

This decision was necessary because the task's instruction ("committed config
uses the env var with a sensible default; the real host path is a
local/compose-override mount") under-specifies *which* file gets the literal
host path — putting it directly in `compose.override.yml` would have broken
that file's existing "no hardcoded absolute paths" convention and made the
file non-portable to a different clone of this repo. Routing it through one
more `${...:-}` indirection resolves that.

### 2.3 `grep_okf` — pure Python, no shell, bounded

```
grep_okf(pattern: str, max_results: int = WIKI_GREP_MAX_RESULTS)
    -> {matches: [{path: str, line_no: int, snippet: str}], truncated: bool}
```

Implementation: `re.compile(pattern, re.IGNORECASE)` (no `shell=True`
subprocess, no f-string-built shell command — the exact classes of injection
named in the task are structurally impossible because no shell is invoked at
all) walked via `os.walk(root, followlinks=False)` over files whose name ends
in `.md`. `followlinks=False` is deliberate: a symlink planted inside the
corpus pointing outside it must not be treated as an in-tree file to scan
(mirrors the `read_okf` containment concern one level up, at directory-walk
time).

Bounds, all in `settings`:
- Stop walking after `WIKI_GREP_MAX_FILES` files scanned (`truncated=True` if
  hit before the walk naturally finished).
- Stop collecting after `min(max_results, WIKI_GREP_MAX_RESULTS)` matches
  (model-supplied `max_results` is a *ceiling request*, never allowed above
  the settings cap — a model asking for `max_results=999999` cannot force a
  corpus dump).
- Each snippet is the matching line, trimmed/truncated to
  `WIKI_GREP_SNIPPET_CHARS` characters — a match on a single absurdly long
  line cannot balloon the tool result.
- `re.error` (invalid pattern, e.g. unbalanced parens) is **not** raised into
  the loop — caught and returned as `{"matches": [], "truncated": False,
  "error": "invalid pattern: <compile error message>"}`, a normal tool-result
  the model can read and retry from (same shape as `run_loop`'s own
  `{"error": "unknown tool ..."}` convention for a bad tool name).
- **Accepted residual risk (documented, not silently ignored): no ReDoS
  timeout.** Python's `re` has no built-in per-call timeout without a third
  dependency (`regex` module or a signal-based wrapper), and adding one is out
  of scope for this feature. Mitigation instead relies on the bounds above:
  the regex is applied once per line of a ≤2000-file, markdown-only corpus
  (median file well under 100KB per the source description), not once per
  full file — a pathological pattern can only be as slow as
  `O(lines × line_length)` regex applications, not `O(file_size)` on a single
  giant buffer, and the whole call still returns within the loop's per-tool
  budget in the worst realistic case for a corpus this size. If the corpus
  grows by an order of magnitude later, revisit with `regex`'s timeout kwarg.

`path` in results is **relative to `WIKI_CORPUS_ROOT`** (POSIX-style, e.g.
`Статьи/foo.md`), never the absolute host/container path — this both matches
what `read_okf` expects as its `path` argument (round-trip: grep → read) and
avoids leaking the container filesystem layout into model context.

### 2.4 `read_okf` — containment-checked, capped

```
read_okf(path: str, offset: int = 0, limit: int = WIKI_READ_MAX_LINES)
    -> {path: str, content: str, truncated: bool}
```

Containment check (reuses the exact idiom already in `app/services/okf.py`,
§1): resolve `WIKI_CORPUS_ROOT`, join `path`, resolve again, then
`.relative_to(root)` inside `try/except ValueError`. On escape attempt (`../`
traversal, absolute path like `/etc/passwd`, or a symlink inside the corpus
that resolves outside it): return `{"error": "path escapes corpus root"}` —
**never** the resolved absolute path in the error (avoid disclosing the
container's filesystem layout to the model/user). On missing file: `{"error":
"file not found"}`. Both are plain tool-result dicts, not exceptions — a
misbehaving/exploratory model call degrades the *answer quality* for that
turn, never the process.

**Deliberate non-decision, stated explicitly:** `read_okf` does **NOT** strip
YAML frontmatter (unlike `okf.py::_strip_frontmatter`, which the *existing*,
unrelated Wiki-page feature uses). Reason: `grep_okf`'s `line_no` is a 1-indexed
line number into the **raw file including frontmatter**; if `read_okf` stripped
the frontmatter before applying `offset`/`limit`, a `line_no` reported by
`grep_okf` would not correspond to the same line `read_okf` returns at that
offset — a silent off-by-N-frontmatter-lines bug that would only show up as
"the model reads the wrong paragraph" with no error anywhere. Keeping both
tools addressing the same raw-line coordinate space is a correctness
requirement, not a style choice.

`offset`/`limit` are line-based (`content.splitlines()[offset:offset+limit]`,
rejoined). `limit` is clamped to `WIKI_READ_MAX_LINES`; the returned text is
additionally hard-capped at `WIKI_READ_CHAR_CAP` characters (mirrors the
`LITSEARCH_FULLTEXT_CHAR_CAP` convention already in this codebase) — whichever
bound triggers first sets `truncated=True`.

### 2.5 `chat.py` wiring

```python
_WIKI_SYSTEM_PROMPT = (
    "Ты — ассистент по внутреннему архиву документов (доклады, статьи, "
    "журналы). Используй grep_okf, чтобы найти релевантные файлы по "
    "корпусу, затем read_okf, чтобы прочитать найденные документы. Отвечай "
    "на русском языке, опираясь только на прочитанные фрагменты, и указывай "
    "путь файла-источника (path) для каждого утверждения. Если ничего "
    "релевантного не нашлось — явно скажи об этом, не выдумывай факты."
)

def _run_wiki_mode(
    session: Session, chat_session_id: uuid.UUID, request: ChatMessageRequest
) -> ChatMessageResponse:
    messages = [
        {"role": "system", "content": _WIKI_SYSTEM_PROMPT},
        {"role": "user", "content": request.content},
    ]
    tools = [wiki_tools.make_grep_tool(), wiki_tools.make_read_tool()]
    outcome = run_loop(
        session, chat_session_id, messages, tools,
        max_iters=settings.WIKI_MAX_ITERS, first_tool_choice="grep_okf",
    )
    degraded = outcome.degraded or outcome.final_text is None
    content = "LLM недоступен — ответ не сформирован." if degraded else outcome.final_text
    mode_used = "degraded" if degraded else "wiki"
    claim = Claim(text=content, experiment_ids=[], confidence=ClaimConfidence.LOW, kind=ClaimKind.FACT)
    session.add(ChatMessage(session_id=chat_session_id, role=ChatRole.ASSISTANT,
                             content=content, message_metadata={"mode_used": mode_used}))
    session.commit()
    return ChatMessageResponse(claims=[claim], summary=content, tools_used=["wiki"],
                                session_id=chat_session_id, mode_used=mode_used)
```

Wired in `answer_message` as an early-return branch, same shape as
`LITERATURE`'s:

```python
if mode == ChatMode.LITERATURE:
    ...
if mode == ChatMode.WIKI:
    return _run_wiki_mode(session, chat_session_id, request)
```

placed **before** `is_gap_click`/`AUTO` handling — `WIKI` is never entered as
part of the `AUTO` waterfall (see §1's rationale), so this early return is
unconditional for `mode == ChatMode.WIKI`, unlike `LITERATURE`'s `primed=True`
vs. `AUTO`'s `primed=False` dual-use of `_run_litsearch_phase_a`.

**Fail-loud contract:** identical to litsearch's Phase A degraded handling
(`chat.py:256-293`) — `outcome.degraded` OR `outcome.final_text is None` ⇒
explicit `"LLM недоступен — ответ не сформирован."` turn with
`mode_used="degraded"`, persisted and returned, **never** a template
"здесь нет данных" masquerading as a real (if empty) answer. This is checked
unconditionally, not only after a tool call — unlike litsearch, wiki mode has
no "did the model even call the tool" branch to worry about (no side panel /
`literature_search_id` gating), so the degraded check collapses to one
condition.

**Frontend (`chat.tsx`):**
- `modeOptions`: add
  ```ts
  {
    value: "wiki",
    label: "Вики",
    hint: "Поиск и чтение локального архива распарсенных документов (доклады, статьи, журналы). Не путать со страницей «Wiki» в боковом меню — это отдельный, более крупный корпус, доступный только модели через поиск.",
  }
  ```
  The hint's second sentence is the concrete mitigation for the §0.1 naming
  collision — it is the only user-facing surface where the two "wiki"s are
  ever mentioned side by side, so it is where the disambiguation has to live.
- `ChatModeUsed` (in `frontend/src/lib/postChatMessage.ts`) gains `"wiki"`;
  `modeUsedLabel["wiki"] = "Вики"`; `modeUsedVariant["wiki"] = "secondary"`
  (matches `knowledge_graph`/`literature`'s variant — an informational badge,
  not `default`/`outline`).
- No new panel/component. The existing "Agent response" card renders Вики's
  `claims`/`summary` exactly as it renders Онтология/Граф today (§1).
- `bash scripts/generate-client.sh` must be re-run after the backend
  `ChatMode` enum changes, to regenerate
  `frontend/src/client/types.gen.ts`'s `ChatMode` union to include `'wiki'`.

## 3. Out of scope

- Any ingest of the 562-file corpus into `Document`/L1/ontology/graph — it
  stays a read-only grounding source for this mode only.
- Folding Вики into the `AUTO` waterfall (future work, if ever wanted).
- A dedicated side panel / progress UI (there is nothing async to show
  progress for).
- Any change to the existing `Wiki` sidebar page, `app/services/wiki.py`,
  `app/schemas/wiki.py`, `app/api/routes/wiki.py`, or `OKF_ROOT`.
- `term_dictionary/`, parser services, graph/ontology sidecar internals, the
  `Document` model — untouched, per the global blast-radius constraint (§4 of
  the plan).
- ReDoS-hardened regex evaluation (accepted risk, §2.3).
- Corpus write/refresh tooling — the corpus is a static, manually-mounted
  read-only drop; re-syncing it is an ops task, not part of this feature.

## 4. Testing strategy

- **Unit, `wiki_tools.py` (fixture corpus, NOT the real 562-doc set):** a
  `tmp_path`-built 2-3-file fake corpus with frontmatter, via
  `monkeypatch.setattr(settings, "WIKI_CORPUS_ROOT", tmp_path)`. Covers:
  `grep_okf` finds a substring/regex match with correct `path`/`line_no`;
  respects `max_results` clamp; sets `truncated` correctly on both the
  file-count and result-count bound; returns `{"error": ...}` on invalid regex
  without raising; does not follow a symlink planted inside the corpus that
  points outside it. `read_okf` returns full content by default; respects
  `offset`/`limit`; hard-caps at `WIKI_READ_CHAR_CAP`; **explicitly rejects**
  `../../../etc/passwd`-style traversal AND an absolute path AND a
  containment-escaping symlink, in each case returning `{"error": "path
  escapes corpus root"}` with no file content and no leaked absolute path;
  returns `{"error": "file not found"}` for a missing-but-in-root path.
  Injection: feed `grep_okf` a pattern containing shell metacharacters
  (`; rm -rf / #`, `` $(whoami) ``) and assert (a) no exception, (b) the
  process/filesystem is untouched (nothing to assert beyond "test still has
  its files" since no subprocess is ever spawned — the absence of a shell call
  is a property of the implementation, verifiable by code inspection + the
  fact these tests use no `subprocess`/`shell=True` anywhere), (c) the pattern
  is treated as a literal/regex string against file contents, matching
  whatever it happens to match (typically nothing).
- **`chat.py` wiring (mirrors `test_chat_literature.py` style, monkeypatching
  `chat_service.run_loop`):** `mode=WIKI` calls `run_loop` with
  `first_tool_choice="grep_okf"` and exactly `[grep_tool, read_tool]`;
  `mode_used="wiki"` + summary on success; `mode_used="degraded"` + the exact
  "LLM недоступен" string + a persisted degraded `ChatMessage` when
  `run_loop` returns `degraded=True` OR `final_text=None`; `AUTO` mode never
  invokes the wiki tools (no wiring into the waterfall).
- **Integration (live gateway, real model, real fixture corpus on disk):** a
  question whose answer only exists in a fixture doc ⇒ model calls
  `grep_okf` then `read_okf` then answers citing the fixture path; verify in
  Langfuse (same gateway/observability as litsearch — no new observability
  work needed, `metadata.session_id` threading is already generic in
  `run_loop`).
- **e2e (Playwright, optional/manual, see plan Task 5):** Вики tab, real
  question against the real mounted 562-doc corpus, sanity-check the answer
  cites a real path under `Доклады/Журналы/Статьи/...`.

## 5. Acceptance criteria

1. `Вики` tab visible in Chat page mode selector, distinct hint text
   disambiguating it from the sidebar `Wiki` page.
2. A question answerable from the mounted corpus produces a grounded answer
   citing at least one real corpus file path, via observed `grep_okf` +
   `read_okf` tool calls (not parametric-knowledge-only).
3. `read_okf("../../../etc/passwd")`, `read_okf("/etc/passwd")`, and a
   corpus-internal symlink pointing outside `WIKI_CORPUS_ROOT` all fail closed
   with `{"error": "path escapes corpus root"}` and zero file content
   returned — verified by an explicit test, not by inspection alone.
4. `grep_okf` never shells out (`shell=True`/`subprocess` with a
   string-joined command) — verified by code review; a pattern containing
   shell metacharacters is inert beyond normal regex matching.
5. A broad `grep_okf` pattern (e.g. `.`) cannot dump the whole corpus into
   context: bounded by `WIKI_GREP_MAX_RESULTS`/`WIKI_GREP_SNIPPET_CHARS`, with
   `truncated=True` surfaced to the model.
6. LLM unreachable ⇒ explicit `mode_used="degraded"` turn with the standard
   "LLM недоступен" text, never a template/empty answer.
7. `run_loop`/`llm.chat` are reused unmodified — no second tool-calling loop
   implementation exists anywhere in the codebase.
8. Existing `Wiki` sidebar page, `OKF_ROOT`, `app/services/wiki.py`,
   `term_dictionary/`, parser, graph/ontology sidecars, and the `Document`
   model are all byte-for-byte untouched by this feature's diff.

## 6. Spec self-review

**Placeholders:** none — every setting has a concrete name/type/default;
every file path (existing and new) is a real repo-relative path verified by
reading the file; the corpus path and file count are verified via `find`, not
assumed.

**Contradictions checked:**
- §2.2 vs. §2.3/§2.4: `WIKI_CORPUS_ROOT` pointing at a nonexistent directory
  (fresh checkout, no `.env` override) must not crash `grep_okf`/`read_okf` —
  confirmed both degrade to empty results / "not found" respectively (an
  `os.walk` over a nonexistent root yields nothing, no exception; a `resolve()`
  + `relative_to()` + is-file check on a nonexistent root's child also just
  returns "not found"). No code path assumes the root exists.
- §2.4's "no frontmatter stripping" vs. the existing `okf.py`'s stripping: this
  is a **deliberate divergence**, justified by the line-coordinate argument,
  not an oversight — flagged explicitly so a future reviewer doesn't "fix" it
  into inconsistency with `grep_okf`.
- §1's claim that wiki tool results must never carry a `"search_id"` key
  (or `run_loop` misinterprets it as a literature search id) was checked
  against the actual `run_loop` source (`if isinstance(tool_result, dict) and
  "search_id" in tool_result: ... uuid.UUID(...)`) — confirmed neither
  `grep_okf` nor `read_okf`'s return shape includes that key.

**Security completeness pass (the two CRITICAL requirements):**
- Path traversal: containment check reuses a pattern already proven in this
  codebase (`okf.py`), extended with an explicit symlink-escape test (the
  existing `okf.py` has no such test today — this spec's plan adds one that
  arguably that older code should also have, but retrofitting `okf.py` is out
  of scope/blast-radius here).
- Shell injection: structurally impossible by construction (pure Python
  `re`/`os.walk`, no `subprocess` at all) rather than "injection-safely
  parameterized" — the strongest available guarantee, and simpler than the
  spec's own suggested `subprocess.run([...], shell=False)` alternative, so
  that alternative is not taken.
- Residual risk called out explicitly (§2.3 ReDoS) rather than silently
  dropped — the honest answer is "bounded exposure, not eliminated," and that
  is stated rather than implied to be solved.

**One open question left for the plan, not resolved here:** whether
`grep_okf`'s `pattern` argument accepts arbitrary Python regex syntax
(power, but a slightly larger invalid-input surface) vs. only literal
substrings (simpler, safer, but the task explicitly says "regex/substring
search," implying regex support is wanted). Resolved in favor of full `re`
syntax with the invalid-pattern-returns-tool-error handling in §2.3 — this is
a implementation-shape decision, not a security one (both shapes are equally
safe against traversal/injection), so it is fine for the plan to just build
it rather than requiring another round of spec review.
