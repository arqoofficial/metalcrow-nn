"""Langfuse trace-attribution wiring (science-knowledge-graph side).

The backend forwards `langfuse_trace_user_id` / `langfuse_session_id` as request
headers; this service must relay them onto the RAG LLM `create()` call as
`extra_headers` AND a mirrored `extra_body.metadata`, so the LiteLLM gateway
attributes the Langfuse trace with the user / session.

No live gateway here — we assert on the exact kwargs handed to the openai SDK
(the boundary LiteLLM parses). End-to-end confirmation (the ids showing on a
trace in the Langfuse UI) needs OPENAI_BASE_URL repointed at the gateway; see
scripts/langfuse_trace_probe.sh.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from science_kg.models import RetrievalContext
from science_kg.rag.generator import _trace_kwargs, generate_answer

_HEADERS = {"langfuse_trace_user_id": "u-9", "langfuse_session_id": "chat-9"}


# ── pure helpers ──────────────────────────────────────────────────────────────


def test_trace_kwargs_builds_headers_and_metadata():
    kw = _trace_kwargs(_HEADERS)
    assert kw["extra_headers"] == _HEADERS
    assert kw["extra_body"] == {
        "metadata": {"trace_user_id": "u-9", "session_id": "chat-9"}
    }


def test_trace_kwargs_empty_is_noop():
    assert _trace_kwargs(None) == {}
    assert _trace_kwargs({}) == {}


def test_trace_kwargs_partial_only_user():
    kw = _trace_kwargs({"langfuse_trace_user_id": "u-1"})
    assert kw["extra_headers"] == {"langfuse_trace_user_id": "u-1"}
    assert kw["extra_body"] == {"metadata": {"trace_user_id": "u-1"}}


def test_route_header_filter_keeps_only_langfuse():
    # importing routes pulls the retriever -> neo4j / spacy stack
    pytest.importorskip("spacy")
    pytest.importorskip("neo4j")
    from science_kg.api.routes import _langfuse_headers

    req = SimpleNamespace(
        headers={
            "langfuse_trace_user_id": "u-1",
            "langfuse_session_id": "s-1",
            "authorization": "Bearer secret",
            "host": "science-kg",
        }
    )
    assert _langfuse_headers(req) == {
        "langfuse_trace_user_id": "u-1",
        "langfuse_session_id": "s-1",
    }


# ── generator: headers reach the LLM create() call ────────────────────────────


def _mock_openai():
    msg = MagicMock()
    msg.content = "ok"
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_generate_answer_forwards_langfuse_to_create():
    ctx = RetrievalContext(nodes=[], edges=[], matched_entities=[], sources=[])
    client = _mock_openai()
    with (
        patch("science_kg.rag.generator.settings.openai_api_key", "test-key"),
        patch("science_kg.rag.generator.openai.AsyncOpenAI", return_value=client),
    ):
        await generate_answer("q", ctx, langfuse_headers=_HEADERS)

    kw = client.chat.completions.create.call_args.kwargs
    assert kw["extra_headers"] == _HEADERS
    assert kw["extra_body"]["metadata"] == {
        "trace_user_id": "u-9",
        "session_id": "chat-9",
    }


@pytest.mark.asyncio
async def test_generate_answer_without_headers_omits_trace_kwargs():
    ctx = RetrievalContext(nodes=[], edges=[], matched_entities=[], sources=[])
    client = _mock_openai()
    with (
        patch("science_kg.rag.generator.settings.openai_api_key", "test-key"),
        patch("science_kg.rag.generator.openai.AsyncOpenAI", return_value=client),
    ):
        await generate_answer("q", ctx)

    kw = client.chat.completions.create.call_args.kwargs
    assert "extra_headers" not in kw
    assert "extra_body" not in kw


# ── endpoint: /rag/query extracts headers and forwards them ───────────────────


@pytest.mark.asyncio
async def test_rag_endpoint_extracts_and_forwards_headers():
    pytest.importorskip("spacy")
    pytest.importorskip("neo4j")
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from science_kg.api.routes import router

    ctx = RetrievalContext(nodes=[], edges=[], matched_entities=[], sources=[])
    captured: dict = {}

    async def _fake_generate(question, context, *, langfuse_headers=None):
        captured["headers"] = langfuse_headers
        return "ok"

    class _FakeRetriever:
        def __init__(self, *a, **k):
            pass

        async def retrieve(self, q):
            return ctx

    app = FastAPI()
    app.include_router(router)
    app.state.graph = MagicMock()

    with (
        patch("science_kg.api.routes.GraphRetriever", _FakeRetriever),
        patch("science_kg.api.routes.generate_answer", _fake_generate),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.post(
                "/api/v1/rag/query",
                json={"question": "q"},
                headers=_HEADERS,
            )

    assert resp.status_code == 200
    assert captured["headers"] == _HEADERS
