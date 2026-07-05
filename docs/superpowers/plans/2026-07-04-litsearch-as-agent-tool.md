# Litsearch as an Agent Tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the procedural `start_search → monitor → synthesize` litsearch pipeline with a real, model-driven OpenAI tool-calling loop: a fast synchronous abstract answer (Phase A, in the web request) followed by a background full-text answer (Phase B, Celery), both driven by the model calling `litsearch_search` / `litsearch_read_fulltext`.

**Architecture:** A generic in-process tool loop (`agent/loop.py::run_loop`) executes native OpenAI tool calls against in-process Python handlers. `llm.chat` is the single gateway transport (threads Langfuse metadata via `extra_body`). Phase A withholds `litsearch_read_fulltext` so the model must answer from abstracts, then dispatches Phase B (`litsearch.agent_continue`) which offers `read_fulltext` and appends full-text answers to `search.answers` so the existing frontend litsearch poll renders them progressively. Terminal `stage` is always driven to `done`/`failed` (try/finally watchdog) so the panel never spins forever.

**Tech Stack:** FastAPI + SQLModel + Postgres, Celery (queue `litsearch`), Alembic, `httpx`, pytest + `respx` (gateway mocked), React + TanStack Query frontend, `article-fetcher` sidecar (pydantic-settings).

## Global Constraints

Every task's requirements implicitly include this section. Copied verbatim from the task brief / spec §2.8–§2.9:

- Only touch litsearch-owned files + chat agent-loop wiring. NEVER touch `term_dictionary/`, the parser, graph, ontology sidecar internals, or the shared `Document` model (schema).
- LLM config committed (not commented-out): `LLM_BASE_URL=https://llm.autumn-lab.uk/v1`, `LLM_MODEL=deepseek/deepseek-v4-flash__or`. All LLM calls go through the gateway (observability).
- Native OpenAI tool-calling loop with in-process handlers. NO prompt-instructed JSON, NO fence-stripping, NO silent template fallback. Fail-loud (explicit degraded turn).
- article-fetcher piracy tiers OFF by default (`scidb_enabled=False`, empty `scihub_mirrors` default). Env-togglable.
- Tests: pytest + `respx` (mock the gateway) for unit; a live-gateway integration path where noted.

## Verified codebase facts (read before starting)

- **Alembic head on `feature/litsearch-chat-integration` is a single head: `a43ee2bced5c`** (`add_litsearch_tables`). Nothing lists it as `down_revision`. **Two-heads gotcha:** before writing the migration in Task 2, run `alembic heads` (or `git log` the versions dir) and chain after the *actual* current head — if a rebase/merge has introduced a second head, do NOT invent a merge; stop and reconcile first.
- `conftest.py` provides a **session-scoped autouse `db: Session`** fixture and module-scoped `client: TestClient` + `superuser_token_headers` / `authentication_token_from_email`. Service tests take `db: Session`; route tests take `client` + auth headers. Follow `tests/services/test_litsearch_start.py` and `tests/worker/test_litsearch_tasks.py` for style (monkeypatch module-level deps; `respx` for the gateway per `tests/services/test_llm.py`).
- `pdf_text.extract_text(pdf_bytes: bytes, *, char_cap: int) -> str`.
- `storage.open_document(*, minio_key: str) -> stream` where stream has `.stream(8192)`, `.close()`, `.release_conn()`.
- Existing helpers to REUSE (keep, move where noted): `litsearch._paper_from_openalex`, `litsearch._mark_fetched`, `litsearch.reconcile`, `litsearch._TERMINAL_FETCH_STATUSES`, `litsearch_client.{search,fetch_async,job_status,fetch_sync}`, `litsearch.add_to_database`, `_paper_provenance`, `_ATTACH_PROVENANCE_TO_DOCUMENT`.
- `.env` already contains the committed `LLM_BASE_URL` / `LLM_MODEL` (lines 75/77). `config.py` default `LLM_MODEL` is still `gpt-4o-mini` and `.env.example` still empty — Task 3 aligns these so the committed config is not misleading.
- `LitStage` enum still has `READING` — it becomes unused after this rework but is NOT dropped (leave the enum member; removing an enum value is a DB migration we do not need).
- No existing `extra_body`/langfuse usage anywhere — Task 3 introduces the metadata threading.

---

## Task 1: article-fetcher — piracy tiers OFF by default

**Files:**
- Modify: `services/article-fetcher/app/config.py:32-35` (`scihub_mirrors` default) and `:61` (`scidb_enabled` default)
- Test: `services/article-fetcher/tests/test_config.py` (append; mirror `test_stc_settings_default_off`)

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.scidb_enabled: bool = False`; `Settings.scihub_mirrors: str = ""`; `Settings.scihub_mirror_list` returns `[]` when unset; both env-togglable via `SCIDB_ENABLED` / `SCIHUB_MIRRORS`.

Note: `fetcher.fetch_article` already gates SciDB on `scidb_enabled` and loops over `scihub_mirror_list` (empty ⇒ no mirror attempts). Only defaults change; no fetch-chain code changes. STC (`stc_enabled`) and headless (`headless_fetch_enabled`) are already `False` — leave them.

- [ ] **Step 1: Write the failing tests**

Append to `services/article-fetcher/tests/test_config.py`:

```python
def test_piracy_tiers_default_off():
    """Compliance (spec §2.9): SciDB + Sci-Hub OFF by default, OA-only."""
    from app.config import Settings

    s = Settings()
    assert s.scidb_enabled is False
    assert s.scihub_mirrors == ""
    assert s.scihub_mirror_list == []


def test_scidb_enabled_from_env(monkeypatch):
    monkeypatch.setenv("SCIDB_ENABLED", "true")
    from app.config import Settings

    s = Settings()
    assert s.scidb_enabled is True


def test_scihub_mirrors_from_env(monkeypatch):
    monkeypatch.setenv("SCIHUB_MIRRORS", "https://sci-hub.ru,https://sci-hub.st")
    from app.config import Settings

    s = Settings()
    assert s.scihub_mirror_list == ["https://sci-hub.ru", "https://sci-hub.st"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/article-fetcher && python -m pytest tests/test_config.py -v`
Expected: `test_piracy_tiers_default_off` FAILS (`scidb_enabled is True`, `scihub_mirrors` non-empty).

- [ ] **Step 3: Change the defaults**

In `services/article-fetcher/app/config.py`, replace the `scihub_mirrors` default (lines 32-35) with:

```python
    # Sci-Hub mirror fallback (tried in order, after the OpenAlex OA path).
    # Comma-separated + env-overridable (SCIHUB_MIRRORS). Default EMPTY per the
    # compliance gate (spec §2.9): piracy tiers OFF unless OSN injects mirrors.
    scihub_mirrors: str = ""
```

And change `scidb_enabled` (line 61):

```python
    # Default OFF per the compliance gate (spec §2.9). Env-togglable (SCIDB_ENABLED).
    scidb_enabled: bool = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/article-fetcher && python -m pytest tests/test_config.py -v`
Expected: PASS. Also run the fetcher suite to confirm no default-mirror assumption broke: `python -m pytest tests/test_fetcher.py tests/test_scidb_downloader.py -v` (should still pass — those tests set flags/mirrors explicitly).

- [ ] **Step 5: Commit**

```bash
git add services/article-fetcher/app/config.py services/article-fetcher/tests/test_config.py
git commit -m "feat(article-fetcher): piracy tiers (SciDB/Sci-Hub) OFF by default, env-togglable"
```

---

## Task 2: Alembic migration + `LiteraturePaper.fulltext_text` column

**Files:**
- Modify: `backend/app/models/litsearch.py:93-94` (add `fulltext_text` field after `fulltext_status`/`fulltext_chars`)
- Create: `backend/app/alembic/versions/<newrev>_add_litpaper_fulltext_text.py`
- Test: `backend/tests/models/test_litsearch_models.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `LiteraturePaper.fulltext_text: str | None` (nullable, default `None`) — persisted extracted full text, read by `litsearch_read_fulltext` (Task 6) so repeat calls don't re-extract.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/models/test_litsearch_models.py`:

```python
def test_literature_paper_persists_fulltext_text(db: Session) -> None:
    from app.models.chat import ChatSession
    from app.models.litsearch import LiteraturePaper, LiteratureSearch
    from tests.utils.user import create_random_user

    user = create_random_user(db)
    cs = ChatSession(user_id=user.id, title="fulltext col test")
    db.add(cs)
    db.commit()
    db.refresh(cs)
    search = LiteratureSearch(session_id=cs.id, question="q?")
    db.add(search)
    db.commit()
    db.refresh(search)

    paper = LiteraturePaper(
        search_id=search.id,
        title="T",
        authors="A",
        abstract="abs",
        fulltext_text="the extracted full text",
    )
    db.add(paper)
    db.commit()
    db.refresh(paper)

    assert paper.fulltext_text == "the extracted full text"
    # default stays None when not provided
    other = LiteraturePaper(search_id=search.id, title="T2", authors="A", abstract="")
    db.add(other)
    db.commit()
    db.refresh(other)
    assert other.fulltext_text is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/models/test_litsearch_models.py::test_literature_paper_persists_fulltext_text -v`
Expected: FAIL — `TypeError: 'fulltext_text' is an invalid keyword argument` (model field missing) OR a DB error (column missing) once the field exists.

- [ ] **Step 3: Add the model field**

In `backend/app/models/litsearch.py`, insert after line 94 (`fulltext_chars`):

```python
    fulltext_chars: int = Field(default=0)
    # Extracted full text, persisted so litsearch_read_fulltext repeat calls
    # don't re-download+re-extract (spec §2.3). NULL until a fetch succeeds.
    fulltext_text: str | None = None
```

- [ ] **Step 4: Generate + hand-edit the migration**

First confirm the head: `cd backend && alembic heads` — expect a single head `a43ee2bced5c`. If two heads appear, STOP and reconcile (do not autogenerate a merge).

Then create `backend/app/alembic/versions/<newrev>_add_litpaper_fulltext_text.py` (use a fresh 12-hex revision id; `<newrev>` below is a placeholder) with:

```python
"""add literature_papers.fulltext_text

Revision ID: <newrev>
Revises: a43ee2bced5c
Create Date: 2026-07-04

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


revision = "<newrev>"
down_revision = "a43ee2bced5c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "literature_papers",
        sa.Column("fulltext_text", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        schema="experiments",
    )


def downgrade():
    op.drop_column("literature_papers", "fulltext_text", schema="experiments")
```

- [ ] **Step 5: Apply the migration and run the test**

Run: `cd backend && alembic upgrade head && python -m pytest tests/models/test_litsearch_models.py::test_literature_paper_persists_fulltext_text -v`
Expected: PASS. Sanity round-trip: `alembic downgrade -1 && alembic upgrade head` succeeds.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/litsearch.py backend/app/alembic/versions/*_add_litpaper_fulltext_text.py backend/tests/models/test_litsearch_models.py
git commit -m "feat(litsearch): add LiteraturePaper.fulltext_text column + migration"
```

---

## Task 3: `llm.chat()` + `ChatResult`; remove legacy synthesis helpers; commit LLM config

**Files:**
- Modify: `backend/app/services/llm.py` (add `ChatResult`, `chat`; keep `complete` as private transport; DELETE `_strip_code_fences`, `complete_json`, `synthesize_from_abstracts`, `read_fulltexts`)
- Modify: `backend/app/core/config.py:114` (`LLM_MODEL` default) — align to committed value
- Modify: `.env.example:81,83` — align to committed values
- Rewrite: `backend/tests/services/test_llm.py` (drop tests for deleted funcs; add `chat` tests)

**Interfaces:**
- Consumes: `settings.LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TIMEOUT`.
- Produces:
  - `class ChatResult` (dataclass): `content: str | None`, `tool_calls: list[dict]` (each `{"id": str, "name": str, "arguments": dict}`), `ok: bool`.
  - `chat(messages, *, tools=None, tool_choice=None, temperature=0.2, metadata=None) -> ChatResult`. On transport/parse failure or empty `LLM_BASE_URL`: `ChatResult(content=None, tool_calls=[], ok=False)`. Threads `metadata` as `extra_body={"metadata": metadata}` in the POST body (top-level `metadata` key — LiteLLM/Langfuse convention).

- [ ] **Step 1: Write the failing tests**

Replace `backend/tests/services/test_llm.py` entirely (keep the `_llm_configured` fixture + the `complete` tests; DELETE the `complete_json` / `synthesize_from_abstracts` / `read_fulltexts` sections) and add a new `chat` section:

```python
import json

import httpx
import pytest
import respx

from app.core.config import settings
from app.services import llm

BASE = "https://llm.example.com/v1"


@pytest.fixture(autouse=True)
def _llm_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_BASE_URL", BASE)
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "deepseek/deepseek-v4-flash__or")
    monkeypatch.setattr(settings, "LLM_TIMEOUT", 60)


# --- complete (retained private transport) ---------------------------------

@respx.mock
def test_complete_returns_content_string() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "Привет"}}]}
        )
    )
    assert llm.complete([{"role": "user", "content": "hi"}]) == "Привет"


# --- chat ------------------------------------------------------------------

@respx.mock
def test_chat_returns_content_and_ok() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "готовый ответ"}}]},
        )
    )
    result = llm.chat([{"role": "user", "content": "hi"}])
    assert result.ok is True
    assert result.content == "готовый ответ"
    assert result.tool_calls == []


@respx.mock
def test_chat_parses_tool_calls_with_arguments_dict() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "litsearch_search",
                                        "arguments": json.dumps({"query": "nickel"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )
    )
    result = llm.chat([{"role": "user", "content": "hi"}])
    assert result.ok is True
    assert result.content is None
    assert result.tool_calls == [
        {"id": "call_1", "name": "litsearch_search", "arguments": {"query": "nickel"}}
    ]


@respx.mock
def test_chat_sends_tools_tool_choice_and_metadata_extra_body() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "x"}}]}
        )
    )
    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    llm.chat(
        [{"role": "user", "content": "hi"}],
        tools=tools,
        tool_choice="auto",
        metadata={"session_id": "sess-1"},
    )
    payload = json.loads(route.calls.last.request.content)
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"
    assert payload["metadata"] == {"session_id": "sess-1"}


def test_chat_returns_not_ok_when_base_url_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    result = llm.chat([{"role": "user", "content": "hi"}])
    assert result.ok is False
    assert result.content is None
    assert result.tool_calls == []


@respx.mock
def test_chat_http_500_returns_not_ok() -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(500))
    result = llm.chat([{"role": "user", "content": "hi"}])
    assert result.ok is False
    assert result.content is None


@respx.mock
def test_chat_malformed_tool_call_arguments_returns_not_ok() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "function": {"name": "t", "arguments": "not json {"},
                                }
                            ],
                        }
                    }
                ]
            },
        )
    )
    result = llm.chat([{"role": "user", "content": "hi"}])
    assert result.ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/services/test_llm.py -v`
Expected: the `chat` tests FAIL with `AttributeError: module 'app.services.llm' has no attribute 'chat'`.

- [ ] **Step 3: Implement `chat` + `ChatResult`, delete legacy helpers**

In `backend/app/services/llm.py`: keep `complete`; DELETE `_strip_code_fences`, `complete_json`, `synthesize_from_abstracts`, `read_fulltexts`; add:

```python
from dataclasses import dataclass, field


@dataclass
class ChatResult:
    """Result of one gateway chat-completions round. `ok=False` is the single
    explicit failure signal (transport error, unset base url, or malformed
    payload) — callers NEVER treat a None content as a fabricated answer."""

    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True


def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any | None = None,
    temperature: float = 0.2,
    metadata: dict[str, Any] | None = None,
) -> ChatResult:
    """Native OpenAI tool-calling round-trip against the gateway.

    Returns parsed `content` (may be None when the model only emits tool calls)
    and `tool_calls` as `[{id, name, arguments:dict}]`. `metadata` is threaded
    top-level in the request body (LiteLLM forwards it to Langfuse for trace
    attribution, spec §2.8). Any transport/parse failure -> `ok=False`."""
    if not settings.LLM_BASE_URL:
        return ChatResult(content=None, tool_calls=[], ok=False)
    payload: dict[str, Any] = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if metadata is not None:
        payload["metadata"] = metadata
    try:
        resp = httpx.post(
            f"{settings.LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
            json=payload,
            timeout=settings.LLM_TIMEOUT,
            trust_env=False,
        )
        resp.raise_for_status()
        data: Any = resp.json()
        message = data["choices"][0]["message"]
        content = message.get("content")
        content = content if isinstance(content, str) else None
        raw_calls = message.get("tool_calls") or []
        tool_calls: list[dict[str, Any]] = []
        for call in raw_calls:
            fn = call["function"]
            tool_calls.append(
                {
                    "id": call.get("id", ""),
                    "name": fn["name"],
                    "arguments": json.loads(fn["arguments"] or "{}"),
                }
            )
        return ChatResult(content=content, tool_calls=tool_calls, ok=True)
    except (
        httpx.HTTPError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        logger.warning("LLM chat failed: %s", exc)
        return ChatResult(content=None, tool_calls=[], ok=False)
```

Also update the module docstring (drop the "two synthesis-helpers" / "template fallback" language; describe `chat` as the single tool-calling transport).

- [ ] **Step 4: Align committed LLM config**

In `backend/app/core/config.py:114` change the default to the committed model:

```python
    LLM_MODEL: str = "deepseek/deepseek-v4-flash__or"
```

In `.env.example` set the two lines to the committed, non-commented values:

```
LLM_BASE_URL=https://llm.autumn-lab.uk/v1
LLM_MODEL=deepseek/deepseek-v4-flash__or
```

(`.env` already carries these — leave it.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/services/test_llm.py -v`
Expected: PASS. Then grep to confirm the deleted helpers have no importers left (Tasks 7/8 remove the last callers, but confirm nothing else references them):
`grep -rn "synthesize_from_abstracts\|read_fulltexts\|complete_json\|_strip_code_fences" backend/app` — expect only hits inside `litsearch.py`/`chat.py` that Tasks 7/8 delete. If any OTHER module references them, stop and reassess.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/llm.py backend/app/core/config.py .env.example backend/tests/services/test_llm.py
git commit -m "feat(llm): add chat() tool-calling transport + ChatResult; remove JSON-synthesis helpers; commit LLM config"
```

---

## Task 4: `agent/loop.py::run_loop` — generic tool-calling loop

**Files:**
- Create: `backend/app/services/agent/loop.py`
- Test: `backend/tests/services/test_agent_loop.py`

**Interfaces:**
- Consumes: `llm.chat(...) -> ChatResult` (Task 3).
- Produces:
  - `class Tool` (dataclass): `name: str`, `schema: dict` (OpenAI `{"type":"function","function":{...}}`), `handler: Callable[..., dict]` invoked as `handler(session, chat_session_id, **arguments) -> dict`.
  - `class LoopOutcome` (dataclass): `final_text: str | None`, `tool_calls_made: list[str]`, `literature_search_id: uuid.UUID | None`, `degraded: bool`.
  - `run_loop(session, chat_session_id, messages, tools, *, max_iters, first_tool_choice=None) -> LoopOutcome`. Iter 0 uses `tool_choice=first_tool_choice` (a name → `{"type":"function","function":{"name": name}}`) or `"auto"`; later iters use `"auto"`. On a tool-call response: execute each handler, append the assistant tool-call message + one `role:"tool"` message per call, continue. If a handler result carries a `"search_id"` key, record it as `literature_search_id`. On text-only response: return it. At `max_iters`: one forced `tool_choice="none"` answer turn; still no text ⇒ `degraded=True`. On `ok=False` from `llm.chat`: `degraded=True, final_text=None` (NEVER fabricates text). Every `llm.chat` call passes `metadata={"session_id": str(chat_session_id)}`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/services/test_agent_loop.py`:

```python
import uuid

import pytest

from app.services import llm
from app.services.agent import loop as agent_loop
from app.services.agent.loop import LoopOutcome, Tool, run_loop


def _echo_tool(name: str, result: dict) -> Tool:
    def handler(session, chat_session_id, **kwargs):  # noqa: ANN001, ANN003
        return result

    return Tool(
        name=name,
        schema={"type": "function", "function": {"name": name, "parameters": {}}},
        handler=handler,
    )


class _ScriptedChat:
    """Returns queued ChatResults in order; records each call's tool_choice +
    metadata for assertions."""

    def __init__(self, results: list[llm.ChatResult]) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    def __call__(self, messages, *, tools=None, tool_choice=None,
                 temperature=0.2, metadata=None):  # noqa: ANN001
        self.calls.append(
            {"messages": list(messages), "tool_choice": tool_choice, "metadata": metadata}
        )
        return self._results.pop(0)


def test_run_loop_returns_text_when_no_tool_calls(monkeypatch):
    scripted = _ScriptedChat([llm.ChatResult(content="прямой ответ", ok=True)])
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(None, uuid.uuid4(), [{"role": "user", "content": "hi"}], [], max_iters=4)

    assert isinstance(outcome, LoopOutcome)
    assert outcome.final_text == "прямой ответ"
    assert outcome.degraded is False
    assert outcome.tool_calls_made == []


def test_run_loop_executes_tool_then_returns_final_text(monkeypatch):
    sid = uuid.uuid4()
    search_tool = _echo_tool("litsearch_search", {"search_id": str(sid), "papers": []})
    scripted = _ScriptedChat(
        [
            llm.ChatResult(
                content=None,
                tool_calls=[{"id": "c1", "name": "litsearch_search", "arguments": {"query": "x"}}],
                ok=True,
            ),
            llm.ChatResult(content="ответ по аннотациям", ok=True),
        ]
    )
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(None, uuid.uuid4(), [{"role": "user", "content": "q"}], [search_tool], max_iters=4)

    assert outcome.final_text == "ответ по аннотациям"
    assert outcome.tool_calls_made == ["litsearch_search"]
    assert outcome.literature_search_id == sid
    # role:tool message was appended between the two llm.chat calls
    second_call_msgs = scripted.calls[1]["messages"]
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in second_call_msgs)
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in second_call_msgs)


def test_run_loop_first_tool_choice_only_on_iter0(monkeypatch):
    search_tool = _echo_tool("litsearch_search", {"search_id": str(uuid.uuid4())})
    scripted = _ScriptedChat(
        [
            llm.ChatResult(
                content=None,
                tool_calls=[{"id": "c1", "name": "litsearch_search", "arguments": {}}],
                ok=True,
            ),
            llm.ChatResult(content="done", ok=True),
        ]
    )
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    run_loop(None, uuid.uuid4(), [{"role": "user", "content": "q"}], [search_tool],
             max_iters=4, first_tool_choice="litsearch_search")

    assert scripted.calls[0]["tool_choice"] == {
        "type": "function", "function": {"name": "litsearch_search"}
    }
    assert scripted.calls[1]["tool_choice"] == "auto"


def test_run_loop_threads_session_metadata(monkeypatch):
    scripted = _ScriptedChat([llm.ChatResult(content="ok", ok=True)])
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)
    csid = uuid.uuid4()

    run_loop(None, csid, [{"role": "user", "content": "hi"}], [], max_iters=4)

    assert scripted.calls[0]["metadata"] == {"session_id": str(csid)}


def test_run_loop_forces_final_answer_at_max_iters(monkeypatch):
    tool = _echo_tool("t", {"ok": True})
    # Always returns a tool call; loop must stop at max_iters and force a
    # tool_choice="none" answer turn.
    loop_call = llm.ChatResult(
        content=None, tool_calls=[{"id": "c", "name": "t", "arguments": {}}], ok=True
    )
    scripted = _ScriptedChat([loop_call, loop_call, llm.ChatResult(content="forced final", ok=True)])
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(None, uuid.uuid4(), [{"role": "user", "content": "q"}], [tool], max_iters=2)

    assert outcome.final_text == "forced final"
    assert outcome.degraded is False
    assert scripted.calls[-1]["tool_choice"] == "none"


def test_run_loop_degraded_on_transport_failure(monkeypatch):
    scripted = _ScriptedChat([llm.ChatResult(content=None, tool_calls=[], ok=False)])
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(None, uuid.uuid4(), [{"role": "user", "content": "q"}], [], max_iters=4)

    assert outcome.degraded is True
    assert outcome.final_text is None


def test_run_loop_degraded_when_forced_turn_yields_no_text(monkeypatch):
    tool = _echo_tool("t", {"ok": True})
    loop_call = llm.ChatResult(
        content=None, tool_calls=[{"id": "c", "name": "t", "arguments": {}}], ok=True
    )
    # forced final turn also returns no text -> degraded
    scripted = _ScriptedChat([loop_call, llm.ChatResult(content=None, tool_calls=[], ok=True)])
    monkeypatch.setattr(agent_loop.llm, "chat", scripted)

    outcome = run_loop(None, uuid.uuid4(), [{"role": "user", "content": "q"}], [tool], max_iters=1)

    assert outcome.degraded is True
    assert outcome.final_text is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/services/test_agent_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.agent.loop'`.

- [ ] **Step 3: Implement `run_loop`**

Create `backend/app/services/agent/loop.py`:

```python
"""Generic in-process OpenAI tool-calling loop (spec §2.1). Model-driven: each
iteration asks the gateway (`llm.chat`) for a response; a tool-call response is
executed against in-process handlers and fed back as role:"tool" messages; a
text-only response is the final answer. NEVER fabricates text — an unreachable
LLM yields `degraded=True, final_text=None`, and the caller renders an explicit
degraded turn (spec §2.7)."""

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from app.services import llm

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    name: str
    schema: dict[str, Any]
    handler: Callable[..., dict[str, Any]]


@dataclass
class LoopOutcome:
    final_text: str | None = None
    tool_calls_made: list[str] = field(default_factory=list)
    literature_search_id: uuid.UUID | None = None
    degraded: bool = False


def _named_choice(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name}}


def run_loop(
    session: Session | None,
    chat_session_id: uuid.UUID,
    messages: list[dict[str, Any]],
    tools: list[Tool],
    *,
    max_iters: int,
    first_tool_choice: str | None = None,
) -> LoopOutcome:
    schemas = [t.schema for t in tools] or None
    by_name = {t.name: t for t in tools}
    metadata = {"session_id": str(chat_session_id)}
    outcome = LoopOutcome()

    for iteration in range(max_iters):
        if iteration == 0 and first_tool_choice is not None:
            tool_choice: Any = _named_choice(first_tool_choice)
        else:
            tool_choice = "auto" if schemas else None

        result = llm.chat(
            messages,
            tools=schemas,
            tool_choice=tool_choice,
            metadata=metadata,
        )
        if not result.ok:
            outcome.degraded = True
            return outcome

        if not result.tool_calls:
            outcome.final_text = result.content
            outcome.degraded = result.content is None
            return outcome

        # Echo the assistant tool-call message back into the transcript.
        messages.append(
            {
                "role": "assistant",
                "content": result.content,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                        },
                    }
                    for tc in result.tool_calls
                ],
            }
        )
        for tc in result.tool_calls:
            outcome.tool_calls_made.append(tc["name"])
            tool = by_name.get(tc["name"])
            if tool is None:
                tool_result: dict[str, Any] = {"error": f"unknown tool {tc['name']}"}
            else:
                tool_result = tool.handler(session, chat_session_id, **tc["arguments"])
            if isinstance(tool_result, dict) and "search_id" in tool_result:
                outcome.literature_search_id = uuid.UUID(str(tool_result["search_id"]))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                }
            )

    # Hit max_iters still calling tools: force one no-tools answer turn.
    forced = llm.chat(messages, tools=schemas, tool_choice="none", metadata=metadata)
    if forced.ok and forced.content:
        outcome.final_text = forced.content
        return outcome
    outcome.degraded = True
    outcome.final_text = None
    return outcome
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/services/test_agent_loop.py -v`
Expected: PASS (all 7).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent/loop.py backend/tests/services/test_agent_loop.py
git commit -m "feat(agent): generic run_loop tool-calling loop with degraded contract"
```

---

## Task 5: `litsearch_tools.litsearch_search` handler + shared helpers

**Files:**
- Create: `backend/app/services/litsearch_tools.py`
- Modify: `backend/app/services/litsearch.py` — persist `fulltext_text` in `_mark_fetched` (add one line at both success/failure branches)
- Test: `backend/tests/services/test_litsearch_tools_search.py`

**Interfaces:**
- Consumes: `litsearch_client.{search,fetch_async}`, `litsearch._paper_from_openalex`, `settings.LITSEARCH_MAX_RESULTS`, `LiteratureSearch`, `LiteraturePaper`, `FetchStatus`, `LitStage`; `agent.loop.Tool`.
- Produces:
  - `SEARCH_SCHEMA: dict` — OpenAI function schema for `litsearch_search(query)`.
  - `litsearch_search(session, chat_session_id, *, query, round=0, followup_of=None) -> dict` returns `{"search_id": str, "papers": [{"idx","title","authors","year","doi","abstract"}]}`; persists the `LiteratureSearch` (stage `FETCHING`) + `LiteraturePaper` rows, fires `fetch_async` per fetchable paper.
  - `make_search_tool(*, round=0, followup_of=None) -> Tool` — binds round/followup into a `Tool` whose handler adapts `(session, chat_session_id, **args)`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/services/test_litsearch_tools_search.py`:

```python
from typing import Any

import pytest
from sqlmodel import Session, select

from app.models.chat import ChatSession
from app.models.litsearch import FetchStatus, LiteraturePaper, LiteratureSearch, LitStage
from app.services import litsearch_client, litsearch_tools
from tests.utils.user import create_random_user

_PAPERS: list[dict[str, Any]] = [
    {"doi": "10.1/a", "title": "Paper A", "authors": "A", "year": 2020,
     "abstract": "abs a", "pdf_url": "http://x/a.pdf", "citation_count": 3},
    {"doi": None, "title": "Paper B", "authors": "B", "year": 2021,
     "abstract": "abs b", "pdf_url": None, "citation_count": None},
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

    search = db.exec(select(LiteratureSearch)).one()
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

    search = db.exec(select(LiteratureSearch)).one()
    assert search.round == 1
    assert search.followup_of == parent_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/services/test_litsearch_tools_search.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.litsearch_tools'`.

- [ ] **Step 3: Implement `litsearch_tools` (search half) + persist `fulltext_text`**

First, in `backend/app/services/litsearch.py::_mark_fetched`, persist the text (needed by Task 6). In the `else` branch (success) add `paper.fulltext_text = text`; in the `except` branch set `paper.fulltext_text = None`:

```python
    except Exception:
        logger.warning(...)  # unchanged
        paper.fetch_status = FetchStatus.DONE
        paper.fulltext_status = FulltextStatus.FAILED
        paper.fulltext_chars = 0
        paper.fulltext_text = None
    else:
        paper.fetch_status = FetchStatus.DONE
        paper.fulltext_status = FulltextStatus.ADDED
        paper.fulltext_chars = len(text)
        paper.fulltext_text = text
```

Then create `backend/app/services/litsearch_tools.py`:

```python
"""In-process litsearch tools invoked by the agent loop (spec §2.3). Reuses the
existing OpenAlex search + fetch machinery from `litsearch.py`; adds the
loop-facing handlers and their OpenAI function schemas."""

import logging
import uuid
from typing import Any

from sqlmodel import Session, select

from app.core.config import settings
from app.models.litsearch import FetchStatus, LiteraturePaper, LiteratureSearch, LitStage
from app.services import litsearch, litsearch_client
from app.services.agent.loop import Tool

logger = logging.getLogger(__name__)

SEARCH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "litsearch_search",
        "description": (
            "Search the open scholarly literature (OpenAlex) for papers "
            "relevant to a query. Returns paper abstracts you can use to answer. "
            "Call again with a refined query if the first results are insufficient."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (keywords)."}
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
                row.doi, search.id,
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
    def handler(session: Session, chat_session_id: uuid.UUID, **kwargs: Any) -> dict[str, Any]:
        return litsearch_search(
            session,
            chat_session_id,
            query=kwargs.get("query", ""),
            round=round,
            followup_of=followup_of,
        )

    return Tool(name="litsearch_search", schema=SEARCH_SCHEMA, handler=handler)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/services/test_litsearch_tools_search.py backend/tests/services/test_litsearch_reconcile.py -v`
(reconcile suite confirms the `_mark_fetched` `fulltext_text` addition didn't break existing behavior; adjust path if run from `backend/`.)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/litsearch_tools.py backend/app/services/litsearch.py backend/tests/services/test_litsearch_tools_search.py
git commit -m "feat(litsearch): litsearch_search tool handler + persist fulltext_text in _mark_fetched"
```

---

## Task 6: `litsearch_tools.litsearch_read_fulltext` handler (bound search_id, ≤2 calls)

**Files:**
- Modify: `backend/app/services/litsearch_tools.py` (add `READ_FULLTEXT_SCHEMA`, `make_read_fulltext_tool`)
- Test: `backend/tests/services/test_litsearch_tools_fulltext.py`

**Interfaces:**
- Consumes: `litsearch.reconcile`, `LiteraturePaper.fulltext_text` (Task 2), `FetchStatus`, `FulltextStatus`, `settings.{LITSEARCH_FULLTEXT_CHAR_CAP,LITSEARCH_FETCH_TIMEOUT}`.
- Produces:
  - `READ_FULLTEXT_SCHEMA: dict` — OpenAI schema for `litsearch_read_fulltext()` (no parameters).
  - `make_read_fulltext_tool(search_id) -> Tool` — server-side-bound `search_id` (model passes NO id, spec §2.3). Handler returns `{"papers":[{"idx","title","doi","text"}], "pending": int, "none_available": bool}`. Reconciles fetch jobs to terminal, reads persisted `fulltext_text` (char-capped), counts still-DOWNLOADING papers as `pending`, `none_available=True` when no ADDED text and nothing pending. Guards at **2 calls max** per bound tool (closure counter) — the 3rd+ call returns `{"papers": [], "pending": 0, "none_available": ..., "note": "already read twice"}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/services/test_litsearch_tools_fulltext.py`:

```python
import pytest
from sqlmodel import Session

from app.models.chat import ChatSession
from app.models.litsearch import (
    FetchStatus, FulltextStatus, LiteraturePaper, LiteratureSearch,
)
from app.services import litsearch, litsearch_tools
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


def test_read_fulltext_returns_added_texts_and_counts_pending(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    added = LiteraturePaper(
        search_id=None, title="Ready", authors="A", abstract="",
        doi="10.1/ready", fetch_status=FetchStatus.DONE,
        fulltext_status=FulltextStatus.ADDED, fulltext_text="FULL TEXT BODY",
    )
    downloading = LiteraturePaper(
        search_id=None, title="Slow", authors="B", abstract="",
        fetch_status=FetchStatus.DOWNLOADING, fetch_job_id="jobZ",
    )
    search = _search_with_papers(db, [added, downloading])
    # reconcile leaves the DOWNLOADING paper pending (job still running)
    monkeypatch.setattr(litsearch_client_stub := litsearch, "reconcile",
                        lambda *a, **k: False)  # noqa: ARG005

    tool = litsearch_tools.make_read_fulltext_tool(search.id)
    result = tool.handler(db, search.session_id)

    texts = {p["title"]: p["text"] for p in result["papers"]}
    assert texts == {"Ready": "FULL TEXT BODY"}
    assert result["pending"] == 1
    assert result["none_available"] is False


def test_read_fulltext_none_available_when_no_texts_and_no_pending(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    skipped = LiteraturePaper(
        search_id=None, title="No PDF", authors="A", abstract="",
        fetch_status=FetchStatus.SKIPPED, fulltext_status=FulltextStatus.NONE,
    )
    search = _search_with_papers(db, [skipped])
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)  # noqa: ARG005

    tool = litsearch_tools.make_read_fulltext_tool(search.id)
    result = tool.handler(db, search.session_id)

    assert result["papers"] == []
    assert result["pending"] == 0
    assert result["none_available"] is True


def test_read_fulltext_char_capped(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(litsearch_tools.settings, "LITSEARCH_FULLTEXT_CHAR_CAP", 5)
    added = LiteraturePaper(
        search_id=None, title="Big", authors="A", abstract="",
        fetch_status=FetchStatus.DONE, fulltext_status=FulltextStatus.ADDED,
        fulltext_text="0123456789",
    )
    search = _search_with_papers(db, [added])
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)  # noqa: ARG005

    tool = litsearch_tools.make_read_fulltext_tool(search.id)
    result = tool.handler(db, search.session_id)
    assert result["papers"][0]["text"] == "01234"


def test_read_fulltext_capped_at_two_calls(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    added = LiteraturePaper(
        search_id=None, title="Ready", authors="A", abstract="",
        fetch_status=FetchStatus.DONE, fulltext_status=FulltextStatus.ADDED,
        fulltext_text="BODY",
    )
    search = _search_with_papers(db, [added])
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)  # noqa: ARG005

    tool = litsearch_tools.make_read_fulltext_tool(search.id)
    assert tool.handler(db, search.session_id)["papers"]  # call 1
    assert tool.handler(db, search.session_id)["papers"]  # call 2
    third = tool.handler(db, search.session_id)           # call 3 -> guarded
    assert third["papers"] == []
    assert third["note"] == "already read twice"
```

Note: the first test's `litsearch_client_stub := litsearch` monkeypatch line is intentionally patching `litsearch.reconcile`; simplify to `monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: False)` when implementing.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/services/test_litsearch_tools_fulltext.py -v`
Expected: FAIL — `AttributeError: module 'app.services.litsearch_tools' has no attribute 'make_read_fulltext_tool'`.

- [ ] **Step 3: Implement the read_fulltext half**

Append to `backend/app/services/litsearch_tools.py`:

```python
import time

from app.models.litsearch import FulltextStatus

READ_FULLTEXT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "litsearch_read_fulltext",
        "description": (
            "Read the full texts already downloaded for the current search. "
            "Returns extracted texts plus how many papers are still downloading "
            "(`pending`). Answer from whatever texts are returned; do not call "
            "this more than once to wait for pending downloads."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_MAX_READ_CALLS = 2


def make_read_fulltext_tool(search_id: uuid.UUID) -> Tool:
    """Server-side-bound to `search_id` (spec §2.3): the model passes no id and
    cannot target another search. Capped at two calls per loop."""
    call_count = {"n": 0}

    def handler(session: Session, chat_session_id: uuid.UUID, **kwargs: Any) -> dict[str, Any]:
        now = time.time()
        # Reconcile without forcing (deadline in the future) so still-downloading
        # papers surface as `pending`, not force-failed.
        litsearch.reconcile(
            session, search_id, now_ts=now, deadline_ts=now + settings.LITSEARCH_FETCH_TIMEOUT
        )
        papers = session.exec(
            select(LiteraturePaper).where(LiteraturePaper.search_id == search_id)
        ).all()
        pending = sum(1 for p in papers if p.fetch_status == FetchStatus.DOWNLOADING)

        if call_count["n"] >= _MAX_READ_CALLS:
            return {"papers": [], "pending": pending, "none_available": False,
                    "note": "already read twice"}
        call_count["n"] += 1

        cap = settings.LITSEARCH_FULLTEXT_CHAR_CAP
        ready = [
            {"idx": i, "title": p.title, "doi": p.doi, "text": (p.fulltext_text or "")[:cap]}
            for i, p in enumerate(papers)
            if p.fulltext_status == FulltextStatus.ADDED and p.fulltext_text
        ]
        none_available = not ready and pending == 0
        return {"papers": ready, "pending": pending, "none_available": none_available}

    return Tool(name="litsearch_read_fulltext", schema=READ_FULLTEXT_SCHEMA, handler=handler)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/services/test_litsearch_tools_fulltext.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/litsearch_tools.py backend/tests/services/test_litsearch_tools_fulltext.py
git commit -m "feat(litsearch): litsearch_read_fulltext tool (bound search_id, <=2 calls, pending/none_available)"
```

---

## Task 7: Phase B worker task `litsearch.agent_continue`; remove monitor/synthesize machinery

**Files:**
- Modify: `backend/app/worker/litsearch_tasks.py` — replace `monitor` + `synthesize_task` with `agent_continue`
- Modify: `backend/app/services/litsearch.py` — add `agent_continue(session, search_id, chat_session_id)`; DELETE `synthesize`, `try_begin_reading`, `revert_to_fetching`, `_gather_ready_papers_with_text`, `_NO_FULLTEXT_ANSWER`, `MONITOR_TASK_NAME`, `start_search`, `_template_answer` (last two also referenced by Task 8 — delete here, Task 8 removes callers). Keep `reconcile`, `_mark_fetched`, `_paper_from_openalex`, `add_to_database`, redis lock helpers only if still used (they are not — the per-session lock is dropped per spec §2.11; remove `_get_redis_client`/`_LOCK_TTL_SECONDS`/`_redis_client` if no remaining user).
- Test: `backend/tests/worker/test_litsearch_agent_continue.py` (new); DELETE `backend/tests/worker/test_litsearch_tasks.py` and `backend/tests/services/test_litsearch_synthesize.py`

**Interfaces:**
- Consumes: `agent.loop.run_loop`, `litsearch_tools.{make_search_tool,make_read_fulltext_tool}`, `litsearch.reconcile`, `ChatMessage`, `LiteratureSearch`, `LitStage`.
- Produces:
  - `litsearch.agent_continue(session, search_id, chat_session_id) -> None` — re-seeds `[system, user question, abstract answer]` from DB, runs `run_loop` with `{read_fulltext(bound), search}`, persists each fulltext turn as `ChatMessage(metadata={litsearch_kind:"fulltext", search_id})`, and in a `try/finally` ALWAYS sets a terminal `stage` (`DONE` normally, `FAILED` on exception).
  - Celery task `litsearch.agent_continue` (name string) wrapping the service call in its own `Session`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/worker/test_litsearch_agent_continue.py`:

```python
import uuid

import pytest
from sqlmodel import Session, select

from app.models.chat import ChatMessage, ChatRole, ChatSession
from app.models.litsearch import LiteraturePaper, LiteratureSearch, LitStage, FetchStatus
from app.services import litsearch
from app.services.agent import loop as agent_loop
from app.services.agent.loop import LoopOutcome
from tests.utils.user import create_random_user


def _seed(db: Session) -> tuple[LiteratureSearch, uuid.UUID]:
    user = create_random_user(db)
    cs = ChatSession(user_id=user.id, title="agent_continue test")
    db.add(cs)
    db.commit()
    db.refresh(cs)
    db.add(ChatMessage(session_id=cs.id, role=ChatRole.USER, content="Как извлекают никель?"))
    search = LiteratureSearch(session_id=cs.id, question="Как извлекают никель?", stage=LitStage.FETCHING)
    db.add(search)
    db.commit()
    db.refresh(search)
    db.add(ChatMessage(
        session_id=cs.id, role=ChatRole.ASSISTANT, content="Ответ по аннотациям",
        message_metadata={"litsearch_kind": "abstracts", "search_id": str(search.id)},
    ))
    db.add(LiteraturePaper(
        search_id=search.id, title="P", authors="A", abstract="",
        fetch_status=FetchStatus.DONE,
    ))
    db.commit()
    return search, cs.id


def test_agent_continue_persists_fulltext_turn_and_sets_done(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    search, cs_id = _seed(db)

    def fake_run_loop(session, chat_session_id, messages, tools, *, max_iters, first_tool_choice=None):
        # simulate the model reading fulltext then answering
        return LoopOutcome(final_text="Ответ по полным текстам", tool_calls_made=["litsearch_read_fulltext"])

    monkeypatch.setattr(litsearch, "run_loop", fake_run_loop)
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)  # noqa: ARG005

    litsearch.agent_continue(db, search.id, cs_id)

    db.refresh(search)
    assert search.stage == LitStage.DONE

    fulltext_msgs = [
        m for m in db.exec(
            select(ChatMessage).where(ChatMessage.session_id == cs_id)
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

    def fake_run_loop(session, chat_session_id, messages, tools, *, max_iters, first_tool_choice=None):
        captured["messages"] = list(messages)
        captured["tool_names"] = [t.name for t in tools]
        return LoopOutcome(final_text="ok")

    monkeypatch.setattr(litsearch, "run_loop", fake_run_loop)
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)  # noqa: ARG005

    litsearch.agent_continue(db, search.id, cs_id)

    roles = [m["role"] for m in captured["messages"]]
    assert roles == ["system", "user", "assistant"]
    assert captured["messages"][1]["content"] == "Как извлекают никель?"
    assert captured["messages"][2]["content"] == "Ответ по аннотациям"
    assert "litsearch_read_fulltext" in captured["tool_names"]
    assert "litsearch_search" in captured["tool_names"]


def test_agent_continue_sets_failed_on_exception(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    search, cs_id = _seed(db)

    def boom(*a, **k):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("loop blew up")

    monkeypatch.setattr(litsearch, "run_loop", boom)
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)  # noqa: ARG005

    litsearch.agent_continue(db, search.id, cs_id)  # must not raise

    db.refresh(search)
    assert search.stage == LitStage.FAILED


def test_agent_continue_degraded_persists_explicit_turn(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    search, cs_id = _seed(db)
    monkeypatch.setattr(
        litsearch, "run_loop",
        lambda *a, **k: LoopOutcome(final_text=None, degraded=True),  # noqa: ARG005
    )
    monkeypatch.setattr(litsearch, "reconcile", lambda *a, **k: True)  # noqa: ARG005

    litsearch.agent_continue(db, search.id, cs_id)

    db.refresh(search)
    assert search.stage == LitStage.DONE  # settled, not stranded
    msgs = db.exec(
        select(ChatMessage).where(ChatMessage.session_id == cs_id)
        .where(ChatMessage.role == ChatRole.ASSISTANT)
    ).all()
    degraded = [m for m in msgs if (m.message_metadata or {}).get("mode_used") == "degraded"]
    assert len(degraded) == 1
    assert "LLM недоступен" in degraded[0].content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/worker/test_litsearch_agent_continue.py -v`
Expected: FAIL — `AttributeError: module 'app.services.litsearch' has no attribute 'agent_continue'`.

- [ ] **Step 3: Implement `agent_continue` and remove the old machinery**

In `backend/app/services/litsearch.py`:
- Add imports at module level: `from app.services.agent.loop import run_loop` and `from app.services import litsearch_tools` (import inside the function to avoid a circular import: `litsearch_tools` imports `litsearch`). Use a function-local import for `litsearch_tools`.
- DELETE: `MONITOR_TASK_NAME`, `_NO_FULLTEXT_ANSWER`, `start_search`, `_template_answer`, `try_begin_reading`, `revert_to_fetching`, `_gather_ready_papers_with_text`, `synthesize`, and the redis lock helpers (`_get_redis_client`, `_redis_client`, `_LOCK_TTL_SECONDS`) if unused.
- Add:

```python
_SYSTEM_PROMPT = (
    "Ты — научный ассистент-металлург. Отвечай по-русски, опираясь только на "
    "данные, которые возвращают инструменты (аннотации и полные тексты статей). "
    "Не выдумывай факты. Сначала прочитай полные тексты (litsearch_read_fulltext); "
    "при необходимости уточни поиск (litsearch_search) один раз, затем дай "
    "обоснованный ответ."
)
_DEGRADED_TEXT = "LLM недоступен — ответ по полным текстам не сформирован."


def agent_continue(
    session: Session, search_id: uuid.UUID, chat_session_id: uuid.UUID
) -> None:
    """Phase B (spec §2.4): re-seed [system, user, abstract] from the DB, run the
    tool loop with read_fulltext + search, persist each full-text turn, and ALWAYS
    drive `stage` to a terminal value (try/finally watchdog, spec §2.11)."""
    from app.services import litsearch_tools  # local import: avoids import cycle

    search = session.get(LiteratureSearch, search_id)
    if search is None:
        logger.error("agent_continue: search %s not found", search_id)
        return
    try:
        now = time.time()
        reconcile(session, search_id, now_ts=now, deadline_ts=now + settings.LITSEARCH_FETCH_TIMEOUT)

        user_msg = session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == chat_session_id)
            .where(ChatMessage.role == ChatRole.USER)
            .order_by(ChatMessage.created_at)
        ).first()
        abstract_msg = next(
            (
                m for m in session.exec(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == chat_session_id)
                    .where(ChatMessage.role == ChatRole.ASSISTANT)
                    .order_by(ChatMessage.created_at)
                ).all()
                if (m.message_metadata or {}).get("litsearch_kind") == "abstracts"
                and (m.message_metadata or {}).get("search_id") == str(search_id)
            ),
            None,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": search.question if user_msg is None else user_msg.content},
        ]
        if abstract_msg is not None:
            messages.append({"role": "assistant", "content": abstract_msg.content})

        tools = [
            litsearch_tools.make_read_fulltext_tool(search_id),
            litsearch_tools.make_search_tool(round=search.round + 1, followup_of=search_id),
        ]
        outcome = run_loop(
            session, chat_session_id, messages, tools,
            max_iters=settings.LITSEARCH_MAX_ROUNDS * 3,
        )

        if outcome.degraded or outcome.final_text is None:
            content = _DEGRADED_TEXT
            meta = {"litsearch_kind": "fulltext", "search_id": str(search_id), "mode_used": "degraded"}
        else:
            content = outcome.final_text
            meta = {"litsearch_kind": "fulltext", "search_id": str(search_id)}
        session.add(ChatMessage(
            session_id=chat_session_id, role=ChatRole.ASSISTANT, content=content,
            message_metadata=meta,
        ))
        session.commit()
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
    finally:
        # Watchdog: force a terminal stage no matter what (spec §2.11).
        settled = session.get(LiteratureSearch, search_id)
        if settled is not None and settled.stage not in (LitStage.DONE, LitStage.FAILED):
            settled.stage = LitStage.DONE
            session.add(settled)
            session.commit()
```

Note: in the tests, `run_loop` and `reconcile` are monkeypatched as attributes of `litsearch`, so keep them referenced as module-level names (`from ... import run_loop`; call `reconcile(...)` — same module). The `_DEGRADED_TEXT` starts with "LLM недоступен" to satisfy the degraded-turn test.

Then rewrite `backend/app/worker/litsearch_tasks.py` entirely:

```python
"""Celery task for Phase B of the litsearch agent loop (spec §2.4), queue
`litsearch`. `agent_continue` runs the slow full-text tool loop off the web
request; it opens its own short-lived DB session (no request-scoped session in a
worker process)."""

import logging
import uuid

from sqlmodel import Session

from app.core.db import engine
from app.services import litsearch
from app.services.tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="litsearch.agent_continue")  # type: ignore[untyped-decorator]
def agent_continue(search_id: str, chat_session_id: str) -> None:
    with Session(engine) as session:
        litsearch.agent_continue(session, uuid.UUID(search_id), uuid.UUID(chat_session_id))
```

Delete `backend/tests/worker/test_litsearch_tasks.py` and `backend/tests/services/test_litsearch_synthesize.py` (they test removed code). Also remove `backend/tests/services/test_litsearch_start.py` (tests deleted `start_search`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/worker/test_litsearch_agent_continue.py -v`
Expected: PASS (4 tests). Then confirm nothing imports the removed symbols:
`grep -rn "start_search\|litsearch.monitor\|litsearch.synthesize\|try_begin_reading\|revert_to_fetching\|_gather_ready_papers_with_text" backend/app` — expect zero hits (Task 8 removes the last `start_search` caller; if `chat.py` still references it, that's fixed in Task 8, so run Task 8 before the full suite).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/litsearch.py backend/app/worker/litsearch_tasks.py backend/tests/worker/test_litsearch_agent_continue.py
git rm backend/tests/worker/test_litsearch_tasks.py backend/tests/services/test_litsearch_synthesize.py backend/tests/services/test_litsearch_start.py
git commit -m "feat(litsearch): Phase B agent_continue task; remove monitor/synthesize/reading machinery"
```

---

## Task 8: `chat.py::answer_message` Phase A; remove `_literature_answer` / templates

**Files:**
- Modify: `backend/app/services/chat.py` — rewrite the LITERATURE branch + add AUTO litsearch; DELETE `_literature_answer`; ensure no reference to removed `litsearch.start_search`
- Test: `backend/tests/services/test_chat_literature.py` (rewrite)

**Interfaces:**
- Consumes: `agent.loop.run_loop`, `litsearch_tools.{make_search_tool,SEARCH_SCHEMA}`, `agent.{hybrid_search,...}` (existing AUTO tools stay via the legacy branches for ontology/KG — see note), `celery_app.signature("litsearch.agent_continue", ...)`, `LiteraturePaper`, `LiteratureRef`.
- Produces: `answer_message` where LITERATURE (and AUTO-with-litsearch) runs Phase A synchronously: `run_loop` with the fast toolset (`litsearch_search` + AUTO's `hybrid_search`/ontology/KG NOT added to the loop in this iteration — see scope note), withholding `read_fulltext`. On a litsearch tool use: persist the user row, persist the abstract answer `ChatMessage(litsearch_kind:"abstracts", search_id)`, set `ChatMessageResponse.literature = LiteratureRef(search_id, paper_count)`, dispatch `litsearch.agent_continue(search_id, chat_session_id)`, return the abstract answer. On no litsearch use in AUTO: existing ontology/KG waterfall unchanged.

**Scope note (resolve spec ambiguity — see summary):** The spec §2.4 lists `hybrid_search`/`ontology`/`knowledge_graph` as AUTO Phase-A tools *alongside* `litsearch_search`. Those three are NOT yet in-process `Tool`s wired for the loop (ontology/KG are legacy `if/elif` branches, spec §2.5 keeps them "unchanged legacy branches; out of scope"). To honor "only touch litsearch-owned files + chat agent-loop wiring" and §2.5, this task wires **only `litsearch_search` into the loop** and preserves the ontology→KG waterfall as the fallback when the loop is not used. AUTO gets litsearch by running the loop *first* with `tool_choice="auto"` and a single `litsearch_search` tool: if the model calls it, Phase B proceeds and the panel lights up (fixes I5); if the model answers with plain text or the LLM is unreachable, fall through to the existing ontology/KG waterfall. LITERATURE primes `first_tool_choice="litsearch_search"`.

- [ ] **Step 1: Write the failing tests**

Rewrite `backend/tests/services/test_chat_literature.py`:

```python
import uuid

import pytest
from sqlmodel import Session, select

from app.models.chat import ChatMessage, ChatRole, ChatSession
from app.models.litsearch import LiteraturePaper, LiteratureSearch
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
    db.add(ChatMessage(
        session_id=cs_id, role=ChatRole.ASSISTANT, content="Ответ по аннотациям",
        message_metadata={"litsearch_kind": "abstracts", "search_id": str(search.id)},
    ))
    db.commit()
    return search


def test_literature_mode_runs_phase_a_and_dispatches_phase_b(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = _chat_session(db)
    search_holder: dict = {}

    def fake_run_loop(session, chat_session_id, messages, tools, *, max_iters, first_tool_choice=None):
        assert first_tool_choice == "litsearch_search"
        # withheld: read_fulltext not offered in Phase A
        assert [t.name for t in tools] == ["litsearch_search"]
        s = _seed_search(session, chat_session_id)
        search_holder["id"] = s.id
        return LoopOutcome(final_text="Ответ по аннотациям", tool_calls_made=["litsearch_search"],
                           literature_search_id=s.id)

    dispatched: list = []
    monkeypatch.setattr(chat_service, "run_loop", fake_run_loop)
    monkeypatch.setattr(chat_service, "_dispatch_agent_continue",
                        lambda search_id, cs_id: dispatched.append((search_id, cs_id)))

    req = ChatMessageRequest(content="Как извлекают никель?",
                             metadata=ChatMessageMetadata(mode=ChatMode.LITERATURE))
    response = chat_service.answer_message(db, cs.id, req)

    assert response.literature is not None
    assert response.literature.search_id == search_holder["id"]
    assert response.mode_used == "literature"
    assert response.summary == "Ответ по аннотациям"
    assert dispatched == [(search_holder["id"], cs.id)]

    # user row persisted
    user_rows = db.exec(
        select(ChatMessage).where(ChatMessage.session_id == cs.id)
        .where(ChatMessage.role == ChatRole.USER)
    ).all()
    assert len(user_rows) == 1


def test_literature_mode_degraded_when_llm_unreachable(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = _chat_session(db)
    monkeypatch.setattr(chat_service, "run_loop",
                        lambda *a, **k: LoopOutcome(final_text=None, degraded=True))  # noqa: ARG005
    monkeypatch.setattr(chat_service, "_dispatch_agent_continue", lambda *a, **k: None)  # noqa: ARG005

    req = ChatMessageRequest(content="q", metadata=ChatMessageMetadata(mode=ChatMode.LITERATURE))
    response = chat_service.answer_message(db, cs.id, req)

    assert response.mode_used == "degraded"
    assert "LLM недоступен" in response.summary
    assert response.literature is None


def test_auto_mode_uses_litsearch_when_model_calls_it(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = _chat_session(db)

    def fake_run_loop(session, chat_session_id, messages, tools, *, max_iters, first_tool_choice=None):
        assert first_tool_choice is None  # AUTO does not prime
        s = _seed_search(session, chat_session_id)
        return LoopOutcome(final_text="Ответ по аннотациям", tool_calls_made=["litsearch_search"],
                           literature_search_id=s.id)

    monkeypatch.setattr(chat_service, "run_loop", fake_run_loop)
    monkeypatch.setattr(chat_service, "_dispatch_agent_continue", lambda *a, **k: None)  # noqa: ARG005

    req = ChatMessageRequest(content="никель", metadata=ChatMessageMetadata(mode=ChatMode.AUTO))
    response = chat_service.answer_message(db, cs.id, req)
    assert response.literature is not None
    assert response.mode_used == "literature"


def test_auto_mode_falls_through_to_waterfall_when_no_litsearch(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = _chat_session(db)
    # loop returns a plain text answer with no litsearch tool call -> AUTO must
    # NOT treat it as literature; falls through to ontology/KG waterfall.
    monkeypatch.setattr(chat_service, "run_loop",
                        lambda *a, **k: LoopOutcome(final_text=None, degraded=True))  # noqa: ARG005
    monkeypatch.setattr(chat_service, "_ontology_claims", lambda q: ([], []))  # noqa: ARG005
    monkeypatch.setattr(chat_service.agent, "hybrid_search",
                        lambda s, r: __import__("app.schemas.search", fromlist=["SearchResponse"]).SearchResponse(results=[], total=0))  # noqa: ARG005
    monkeypatch.setattr(chat_service.science_kg_client, "rag_query", lambda q: None)  # noqa: ARG005

    req = ChatMessageRequest(content="привет", metadata=ChatMessageMetadata(mode=ChatMode.AUTO))
    response = chat_service.answer_message(db, cs.id, req)
    assert response.mode_used == "knowledge_graph"
    assert response.literature is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/services/test_chat_literature.py -v`
Expected: FAIL — `AttributeError: module 'app.services.chat' has no attribute 'run_loop'` / `_dispatch_agent_continue`.

- [ ] **Step 3: Rewrite the litsearch path in `chat.py`**

In `backend/app/services/chat.py`:
- Replace `from app.services import agent, litsearch, ontology_client, science_kg_client` with an import that also brings the loop pieces:

```python
from app.services import agent, ontology_client, science_kg_client, litsearch_tools
from app.services.agent.loop import run_loop
from app.services.tasks import celery_app, _apply_async_safe
```

- DELETE `_literature_answer` entirely.
- Add helpers + a Phase-A runner:

```python
_LITSEARCH_SYSTEM_PROMPT = (
    "Ты — научный ассистент-металлург. Если вопрос требует научной литературы, "
    "вызови инструмент litsearch_search и ответь по аннотациям найденных статей "
    "на русском языке, опираясь только на них. Не выдумывай факты."
)


def _dispatch_agent_continue(search_id: uuid.UUID, chat_session_id: uuid.UUID) -> None:
    _apply_async_safe(
        celery_app.signature(
            "litsearch.agent_continue", args=[str(search_id), str(chat_session_id)]
        )
    )


def _run_litsearch_phase_a(
    session: Session,
    chat_session_id: uuid.UUID,
    request: ChatMessageRequest,
    *,
    primed: bool,
) -> ChatMessageResponse | None:
    """Phase A (spec §2.4). Returns a ChatMessageResponse when the model used
    litsearch (or degraded under LITERATURE); returns None when AUTO should fall
    through to the ontology/KG waterfall."""
    messages = [
        {"role": "system", "content": _LITSEARCH_SYSTEM_PROMPT},
        {"role": "user", "content": request.content},
    ]
    tools = [litsearch_tools.make_search_tool(round=0)]  # read_fulltext WITHHELD
    outcome = run_loop(
        session, chat_session_id, messages, tools,
        max_iters=4,
        first_tool_choice="litsearch_search" if primed else None,
    )

    if outcome.literature_search_id is None:
        if primed:
            # LITERATURE always yields a literature turn, even degraded.
            content = (
                "LLM недоступен — ответ не сформирован."
                if outcome.degraded or outcome.final_text is None
                else outcome.final_text
            )
            session.add(ChatMessage(
                session_id=chat_session_id, role=ChatRole.ASSISTANT, content=content,
                message_metadata={"mode_used": "degraded"},
            ))
            session.commit()
            return ChatMessageResponse(
                claims=[Claim(text=content, experiment_ids=[], confidence=ClaimConfidence.LOW, kind=ClaimKind.FACT)],
                summary=content, tools_used=["litsearch"], session_id=chat_session_id,
                mode_used="degraded",
            )
        return None  # AUTO fall-through

    search_id = outcome.literature_search_id
    summary = outcome.final_text or ""
    session.add(ChatMessage(
        session_id=chat_session_id, role=ChatRole.ASSISTANT, content=summary,
        message_metadata={"litsearch_kind": "abstracts", "search_id": str(search_id)},
    ))
    session.commit()

    paper_count = len(session.exec(
        select(LiteraturePaper).where(LiteraturePaper.search_id == search_id)
    ).all())

    _dispatch_agent_continue(search_id, chat_session_id)

    return ChatMessageResponse(
        claims=[Claim(text=summary, experiment_ids=[], confidence=ClaimConfidence.LOW, kind=ClaimKind.FACT)],
        summary=summary, tools_used=["litsearch"], session_id=chat_session_id,
        mode_used="literature",
        literature=LiteratureRef(search_id=search_id, paper_count=paper_count),
    )
```

- In `answer_message`, after persisting the user row (keep that block) and computing `mode`, add the litsearch dispatch BEFORE the ontology/KG waterfall:

```python
    if mode == ChatMode.LITERATURE:
        result = _run_litsearch_phase_a(session, chat_session_id, request, primed=True)
        assert result is not None  # LITERATURE always returns a response
        return result

    is_gap_click = bool(...)  # unchanged

    if not is_gap_click and mode == ChatMode.AUTO:
        lit = _run_litsearch_phase_a(session, chat_session_id, request, primed=False)
        if lit is not None:
            return lit
    # ... fall through to the existing KNOWLEDGE_GRAPH/ONTOLOGY/auto-waterfall
```

Keep the existing gap_click / KNOWLEDGE_GRAPH / ONTOLOGY / auto-waterfall branches and the final assistant-row persistence for those branches untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/services/test_chat_literature.py tests/services/test_chat_ontology.py -v`
Expected: PASS (litsearch tests pass; ontology tests still pass — waterfall preserved). Then the API-level test: `python -m pytest tests/api/routes/test_chat.py -v`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/chat.py backend/tests/services/test_chat_literature.py
git commit -m "feat(chat): Phase A litsearch tool loop for LITERATURE + AUTO; remove _literature_answer/templates"
```

---

## Task 9: `GET /api/v1/utils/llm-health` — real gateway round-trip

**Files:**
- Modify: `backend/app/api/routes/utils.py`
- Test: `backend/tests/api/routes/test_utils_llm_health.py`

**Interfaces:**
- Consumes: `llm.chat` (Task 3).
- Produces: `GET /api/v1/utils/llm-health -> {"ok": bool, "model": str}`. Performs a real minimal `llm.chat([{"role":"user","content":"ping"}])`; `ok` mirrors `ChatResult.ok` (spec §2.7 — a misconfigured/unreachable LLM is visible, not silent). No auth dependency (health probe), consistent with `health-check/`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/routes/test_utils_llm_health.py`:

```python
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.config import settings

BASE = "https://llm.example.com/v1"


@pytest.fixture(autouse=True)
def _cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_BASE_URL", BASE)
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "deepseek/deepseek-v4-flash__or")


@respx.mock
def test_llm_health_ok(client: TestClient) -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})
    )
    r = client.get(f"{settings.API_V1_STR}/utils/llm-health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["model"] == "deepseek/deepseek-v4-flash__or"


@respx.mock
def test_llm_health_reports_unreachable(client: TestClient) -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(502))
    r = client.get(f"{settings.API_V1_STR}/utils/llm-health")
    assert r.status_code == 200
    assert r.json()["ok"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/api/routes/test_utils_llm_health.py -v`
Expected: FAIL — 404 (route not defined).

- [ ] **Step 3: Add the route**

In `backend/app/api/routes/utils.py` add:

```python
from app.services import llm


@router.get("/llm-health")
def llm_health() -> dict[str, object]:
    """Real minimal gateway round-trip so a misconfigured/unreachable LLM is
    visible, not silent (spec §2.7)."""
    result = llm.chat([{"role": "user", "content": "ping"}])
    return {"ok": result.ok, "model": settings.LLM_MODEL}
```

Add `from app.core.config import settings` to the imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/api/routes/test_utils_llm_health.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/utils.py backend/tests/api/routes/test_utils_llm_health.py
git commit -m "feat(utils): GET /utils/llm-health real gateway round-trip"
```

---

## Task 10: Frontend — AUTO tab surfaces the literature panel

**Files:**
- Modify (if needed): `frontend/src/routes/_layout/chat.tsx:434-464` (the "Agent response" card condition) and verify `:210-211` panel wiring
- Test: manual/e2e (no unit-test harness for this route today; verify via `run` skill / Playwright in Task 11)

**Interfaces:**
- Consumes: `ChatMessageResponse.literature.search_id` (already set by AUTO in Task 8), the existing `getSearch` poll.

Analysis (already-correct wiring): The panel is driven by `effectiveSearchId = activeSearchId ?? latestHistorySearchId` (`chat.tsx:161`), and `activeSearchId` is set from `response.literature?.search_id` in `sendMessageMutation.onSuccess` (`:210-211`) **regardless of mode**. So once Task 8 sets `response.literature` in AUTO, the panel already lights up. The only mode-coupled bit is the "Agent response" summary card, gated on `mode_used !== "literature"` (`:434-435`).

**Minimal change:** when an AUTO turn resolved to litsearch, `mode_used` is `"literature"`, so the summary card is already correctly hidden and the panel shows — no code change required. Confirm this by reading the condition; if a regression is found (e.g. panel not showing in AUTO), the fix is to ensure the `mode_used === "literature"` branch does not early-return before `setActiveSearchId`. It does not (the `onSuccess` sets `activeSearchId` before any render branch).

- [ ] **Step 1: Verify the existing wiring suffices**

Read `frontend/src/routes/_layout/chat.tsx:205-216` and `:434-471`. Confirm:
- `sendMessageMutation.onSuccess` sets `activeSearchId` from `response.literature?.search_id` unconditionally.
- The panel block `{effectiveSearchId && <LiteraturePanel .../>}` (`:467-471`) is not mode-gated.

- [ ] **Step 2: Decide — no change or one-line guard**

If both hold (they do at time of writing), make NO code change; record "verified sufficient" in the task checklist. If a gap is found, apply the smallest fix (do not add streaming, do not restructure). Typecheck either way:

Run: `cd frontend && npm run build` (or `npx tsc --noEmit`)
Expected: passes.

- [ ] **Step 3: Commit (only if a change was made)**

```bash
git add frontend/src/routes/_layout/chat.tsx
git commit -m "fix(chat-ui): surface literature panel in AUTO tab when response.literature is set"
```

If no change: skip the commit and note "no frontend change needed — panel wiring is mode-independent".

---

## Task 11: VERIFY named/forced `tool_choice` on the gateway (verification, not TDD)

**Files:** none (investigation task; records outcome in the PR description / this plan's checklist).

**Goal (spec §1 + acceptance):** Named/forced `tool_choice` support on `deepseek/deepseek-v4-flash__or` via `llm.autumn-lab.uk` is UNVERIFIED. `run_loop`'s `first_tool_choice` and the `tool_choice="none"` forced-final turn depend on it. Verify BEFORE relying on it; if unsupported, fall back to a system-prompt nudge.

- [ ] **Step 1: Curl a forced tool_choice round-trip**

```bash
curl -sS https://llm.autumn-lab.uk/v1/chat/completions \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek/deepseek-v4-flash__or",
    "messages": [{"role":"user","content":"find papers on nickel leaching"}],
    "tools": [{"type":"function","function":{"name":"litsearch_search",
      "parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}}],
    "tool_choice": {"type":"function","function":{"name":"litsearch_search"}}
  }' | python -m json.tool
```
Expected (support confirmed): response `choices[0].message.tool_calls[0].function.name == "litsearch_search"` with a JSON `arguments` string.

- [ ] **Step 2: Curl the `tool_choice:"none"` forced-final turn**

Repeat with `"tool_choice": "none"` and a trailing tool result in the messages; expect a text `content` and NO `tool_calls`.

- [ ] **Step 3: Record the outcome**

- If BOTH work: document "forced tool_choice verified on deepseek-v4-flash via gateway" in the PR body. No code change.
- If forced/named `tool_choice` is REJECTED (HTTP 400 / ignored): in `agent/loop.py`, when `first_tool_choice` is set, instead pass `tool_choice="auto"` AND prepend a system-prompt nudge ("You MUST call litsearch_search first."); for the forced-final turn, drop `tool_choice="none"` and rely on omitting `tools` on the final call. Add a one-line comment citing the gateway limitation. Re-run Task 4's tests (they assert the named-choice payload — relax those two assertions to match the nudge fallback if taken).

- [ ] **Step 4: Observability check (spec §2.8, acceptance §5)**

After a real Phase A + Phase B run against the live gateway, confirm the loop's LLM calls + tool turns appear in **Langfuse (langfuse.autumn-lab.uk)** attributed by `metadata.session_id`, and in the **LiteLLM proxy (llm.autumn-lab.uk)** logs. Record pass/fail in the PR body.

---

## Full-suite gate (run after Task 8, and again after Task 11)

```bash
cd backend && python -m pytest -q
cd ../services/article-fetcher && python -m pytest -q
```
Expected: green. Investigate any test that referenced removed symbols (`start_search`, `synthesize`, `monitor`, `read_fulltexts`, `_template_answer`) — those test files are deleted in Task 7; any lingering importer is a bug to fix, not a test to weaken.

---

## Self-Review: acceptance criteria (spec §5) → task mapping

| Spec §5 criterion | Satisfied by |
|---|---|
| **1.** Литература AND Auto: real-LLM abstract answer (Phase A) then real-LLM fulltext answer (Phase B), driven by `litsearch_search` / `litsearch_read_fulltext` tool calls | Task 4 (loop), Task 5 (search handler), Task 6 (read_fulltext handler), Task 7 (Phase B `agent_continue`), Task 8 (Phase A in LITERATURE + AUTO) |
| **2.** Panel lights up and reaches terminal `done`/`failed` (no infinite poll), any tab; add-to-DB → L1 | Task 5 (sets `stage=FETCHING`, persists rows), Task 7 (try/finally watchdog → terminal stage; §2.11), Task 8 (`response.literature` set for AUTO+LITERATURE), Task 10 (panel wiring verified); add-to-DB unchanged (`add_to_database` kept) |
| **3.** Model can loop (a second `litsearch_search`) on its own decision | Task 4 (`first_tool_choice` only iter 0, then `"auto"`), Task 7 (Phase B toolset includes `make_search_tool(round=+1, followup_of=…)`) |
| **4.** LLM unreachable ⇒ explicit degraded turn, never a template | Task 3 (`ChatResult.ok=False`), Task 4 (degraded contract, no fabricated text), Task 7 (`_DEGRADED_TEXT` + `mode_used:"degraded"`), Task 8 (LITERATURE degraded response) |
| **5.** All LLM/tool calls visible in Langfuse + LiteLLM proxy | Task 3 (`metadata` via body), Task 4 (threads `session_id` on every `llm.chat`), Task 11 step 4 (observability check) |
| **6.** No `synthesize_from_abstracts`/`read_fulltexts`/`complete_json`/`_template_answer`/`_NO_FULLTEXT_ANSWER`/prompt-JSON; no orphaned `monitor`/`synthesize` dispatch | Task 3 (delete llm helpers), Task 7 (delete `synthesize`/`monitor`/`start_search`/`_template_answer`/`_NO_FULLTEXT_ANSWER`/`try_begin_reading`/`revert_to_fetching`), Task 8 (delete `_literature_answer`); grep gates in Tasks 3/7 |
| **7.** Piracy download tiers OFF by default, env-togglable | Task 1 |

**Spec-section coverage skim:** §2.1 loop → Task 4; §2.2 llm → Task 3; §2.3 tools + `fulltext_text` column → Tasks 2/5/6; §2.4 two-phase → Tasks 7/8; §2.5 modes → Task 8 (+§2.5 legacy branches preserved, noted); §2.6 loop safety → Task 4; §2.7 fail-loud + llm-health → Tasks 4/7/8/9; §2.8 observability → Tasks 3/4/11; §2.9 compliance → Task 1; §2.10 kept/removed → Tasks 3/7/8; §2.11 hardening (watchdog, no lock) → Task 7; §2 migration → Task 2. Verification of forced tool_choice (§1) → Task 11.

**Placeholder scan:** no "TBD"/"handle edge cases"/"similar to Task N" — every code step carries full code. `<newrev>` in Task 2 is an explicit instruction to mint a fresh revision id (not a placeholder value to ship).

**Type-consistency check:** `ChatResult{content,tool_calls,ok}` (Task 3) consumed identically in Task 4/9; `Tool{name,schema,handler}` and `LoopOutcome{final_text,tool_calls_made,literature_search_id,degraded}` (Task 4) consumed identically in Tasks 5/6/7/8; `run_loop(session, chat_session_id, messages, tools, *, max_iters, first_tool_choice=None)` signature matches all callers (Tasks 7/8); `litsearch_search(...) -> {"search_id","papers"}` and `make_search_tool`/`make_read_fulltext_tool -> Tool` (Tasks 5/6) match Tasks 7/8 usage; `_dispatch_agent_continue(search_id, chat_session_id)` name matches its monkeypatch in Task 8's tests and the Celery task name `"litsearch.agent_continue"` (Task 7).
