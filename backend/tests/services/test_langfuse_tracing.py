"""Backend side of Langfuse trace attribution.

The chat service derives `langfuse_trace_user_id` / `langfuse_session_id` from
the authenticated user + chat session and forwards them, as httpx request
headers, to both internal KG services (which relay them onto their LLM calls).

These tests pin the two things the backend is responsible for:
  1. building the header dict from (user_id, session_id), and
  2. threading it end-to-end (answer_message -> KG clients -> httpx headers).
"""

import uuid
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

pytest.importorskip("sqlmodel")  # the app.* import chain needs the backend env

from app.services import chat as chat_service  # noqa: E402
from app.services import ontology_client, science_kg_client  # noqa: E402


# ── header builder ────────────────────────────────────────────────────────────


def test_langfuse_headers_builder_full():
    u, s = uuid.uuid4(), uuid.uuid4()
    assert chat_service._langfuse_headers(u, s) == {
        "langfuse_trace_user_id": str(u),
        "langfuse_session_id": str(s),
    }


def test_langfuse_headers_builder_partial_and_empty():
    s = uuid.uuid4()
    assert chat_service._langfuse_headers(None, s) == {
        "langfuse_session_id": str(s)
    }
    assert chat_service._langfuse_headers(None, None) == {}


# ── KG clients forward the headers onto the httpx request ─────────────────────


def _capturing_post(captured: dict[str, Any], json_data: Any):
    def _post(*_a: object, **kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    return _post


def test_rag_query_forwards_langfuse_headers(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        httpx, "post", _capturing_post(captured, {"answer": "ok", "sources": []})
    )
    headers = {"langfuse_trace_user_id": "u-1", "langfuse_session_id": "s-1"}
    science_kg_client.rag_query("q", langfuse_headers=headers)
    assert captured["headers"] == headers


def test_rag_query_without_headers_sends_none(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        httpx, "post", _capturing_post(captured, {"answer": "ok", "sources": []})
    )
    science_kg_client.rag_query("q")
    assert captured["headers"] is None


def test_ontology_ask_forwards_langfuse_headers(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}
    monkeypatch.setattr(httpx, "post", _capturing_post(captured, {"claims": []}))
    headers = {"langfuse_trace_user_id": "u-2", "langfuse_session_id": "s-2"}
    ontology_client.ask("q", langfuse_headers=headers)
    assert captured["headers"] == headers


# ── answer_message threads user_id + session_id all the way down ───────────────


class _FakeSession:
    """Minimal stand-in: the tracing path must not need a real DB. get() -> None
    short-circuits autotitling; add()/commit() are no-ops; exec() must not run."""

    def get(self, *_a: object, **_k: object) -> None:
        return None

    def add(self, *_a: object, **_k: object) -> None:
        pass

    def commit(self) -> None:
        pass

    def exec(self, *_a: object, **_k: object):  # pragma: no cover - guard
        raise AssertionError("tracing test should not hit the DB")


def test_answer_message_threads_user_and_session(monkeypatch: pytest.MonkeyPatch):
    from app.schemas.chat import (
        ChatMessageRequest,
        Claim,
        ClaimConfidence,
        ClaimKind,
    )

    captured: dict[str, Any] = {}

    # auto mode: ontology answers empty -> falls through to knowledge_graph
    monkeypatch.setattr(
        chat_service, "_ontology_claims",
        lambda q, *, langfuse_headers=None: ([], []),
    )

    def _kg(session, request, *, langfuse_headers=None):
        captured["headers"] = langfuse_headers
        return (
            Claim(
                text="x",
                experiment_ids=[],
                confidence=ClaimConfidence.LOW,
                kind=ClaimKind.FACT,
            ),
            ["hybrid_search"],
            "x",
        )

    monkeypatch.setattr(chat_service, "_knowledge_graph_answer", _kg)
    monkeypatch.setattr(chat_service, "_recent_history", lambda *_a, **_k: [])
    monkeypatch.setattr(chat_service.agent_llm, "is_configured", lambda: False)

    user_id, session_id = uuid.uuid4(), uuid.uuid4()
    chat_service.answer_message(
        _FakeSession(),
        session_id,
        ChatMessageRequest(content="q"),
        user_id=user_id,
    )

    assert captured["headers"] == {
        "langfuse_trace_user_id": str(user_id),
        "langfuse_session_id": str(session_id),
    }
