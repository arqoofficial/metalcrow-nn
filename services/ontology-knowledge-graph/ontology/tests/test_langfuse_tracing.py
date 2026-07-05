# -*- coding: utf-8 -*-
"""Langfuse trace attribution on the ontology intent-classification LLM call.

Chain: POST /api/v1/ask -> agent.answer -> route -> intent_llm.classify. The
forwarded `langfuse_*` headers must ride down to `classify`, which relays them
onto its `create()` call as `extra_headers` + a mirrored `extra_body.metadata`
so the LiteLLM gateway attributes the Langfuse trace (user / session).

Boundary tests only (no live gateway, no Postgres). End-to-end confirmation of
a trace in the Langfuse UI needs LLM_BASE_URL repointed at the gateway; see
scripts/langfuse_trace_probe.sh.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ontology.mocks import intent_llm
from ontology.mocks.intent_llm import _trace_kwargs

_HEADERS = {"langfuse_trace_user_id": "u-3", "langfuse_session_id": "chat-3"}


# ── pure helpers ──────────────────────────────────────────────────────────────


def test_trace_kwargs_headers_and_metadata():
    kw = _trace_kwargs(_HEADERS)
    assert kw["extra_headers"] == _HEADERS
    assert kw["extra_body"] == {
        "metadata": {"trace_user_id": "u-3", "session_id": "chat-3"}
    }


def test_trace_kwargs_empty():
    assert _trace_kwargs(None) == {}
    assert _trace_kwargs({}) == {}


def test_service_header_filter_keeps_only_langfuse():
    # importing tool_service pulls the store -> psycopg stack
    pytest.importorskip("psycopg")
    from ontology.tool_service import _langfuse_headers

    req = SimpleNamespace(
        headers={
            "langfuse_trace_user_id": "u-1",
            "langfuse_session_id": "s-1",
            "content-type": "application/json",
        }
    )
    assert _langfuse_headers(req) == {
        "langfuse_trace_user_id": "u-1",
        "langfuse_session_id": "s-1",
    }


# ── classify: headers reach the LLM create() call ─────────────────────────────


def _fake_client(captured: dict):
    msg = MagicMock()
    msg.content = '{"intent": "search_passages"}'
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]

    def _create(**kwargs):
        captured.update(kwargs)
        return resp

    client = MagicMock()
    client.chat.completions.create = _create
    return client


def test_classify_forwards_headers_to_create(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}
    monkeypatch.setattr(intent_llm, "_ensure_client", lambda: _fake_client(captured))
    monkeypatch.setattr(intent_llm, "_model", "Openai/Gpt-oss-120b")

    intent_llm.classify("q", langfuse_headers=_HEADERS)

    assert captured["extra_headers"] == _HEADERS
    assert captured["extra_body"]["metadata"] == {
        "trace_user_id": "u-3",
        "session_id": "chat-3",
    }


def test_classify_without_headers_omits_trace_kwargs(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}
    monkeypatch.setattr(intent_llm, "_ensure_client", lambda: _fake_client(captured))
    monkeypatch.setattr(intent_llm, "_model", "Openai/Gpt-oss-120b")

    intent_llm.classify("q")

    assert "extra_headers" not in captured
    assert "extra_body" not in captured


# ── route threads headers to classify ─────────────────────────────────────────


def test_route_forwards_headers_to_classify(monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("psycopg")  # importing agent pulls the store stack
    from ontology.mocks import agent

    captured: dict = {}
    monkeypatch.setattr(agent, "_USE_LLM_INTENT", True)
    monkeypatch.setattr(agent, "_is_chitchat", lambda low: False)
    monkeypatch.setattr(agent, "detect_intent", lambda store, q: ("chitchat", {}))

    def _classify(question, *, langfuse_headers=None):
        captured["headers"] = langfuse_headers
        return None  # -> route falls back to the (stubbed) detect_intent

    monkeypatch.setattr(agent.intent_llm, "classify", _classify)

    agent.route(None, "какой процесс даёт максимальное извлечение?", langfuse_headers=_HEADERS)

    assert captured["headers"] == _HEADERS
