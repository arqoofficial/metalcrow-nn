import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.schemas.chat import ChatMessageResponse
from app.services import ontology_client


def test_ontology_claims_maps_citations() -> None:
    from app.services import chat as chat_service

    original = ontology_client.ask
    try:
        ontology_client.ask = lambda _q: {
            "tools_used": ["literature_review"],
            "tool_args": {"process": "хлорирование"},
            "claims": [
                {
                    "text": "Метод «chlorination»: 1 док.",
                    "kind": "review",
                    "confidence": "high",
                    "citations": ["цитата из отчёта"],
                }
            ],
        }
        claims, tools = chat_service._ontology_claims("что известно про хлорирование?")
    finally:
        ontology_client.ask = original

    assert tools == ["ontology:literature_review"]
    assert len(claims) == 1
    assert "цитата из отчёта" in claims[0].text
    assert "[review]" in claims[0].text


def test_ontology_claims_ignores_empty_evidence_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import chat as chat_service

    monkeypatch.setattr(
        ontology_client,
        "ask",
        lambda _q: {
            "tools_used": ["evidence"],
            "tool_args": {},
            "claims": [{"text": "recovery_degree = 95–97 %", "kind": "fact"}],
        },
    )
    claims, tools = chat_service._ontology_claims("супер")
    assert claims == []
    assert tools == []


def test_ontology_claims_ignores_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import chat as chat_service

    monkeypatch.setattr(
        ontology_client,
        "ask",
        lambda _q: {"tools_used": [], "tool_args": {}, "claims": []},
    )
    claims, tools = chat_service._ontology_claims("random english question")
    assert claims == []
    assert tools == []


def _create_session(
    client: TestClient, headers: dict[str, str], title: str = "test"
) -> dict[str, Any]:
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions",
        headers=headers,
        json={"title": title},
    )
    assert r.status_code == 200
    return r.json()


def _parse_sse(response_text: str) -> ChatMessageResponse:
    assert response_text.startswith("data: ")
    payload = response_text[len("data: ") :].strip()
    return ChatMessageResponse.model_validate(json.loads(payload))


def test_post_message_ontology_branch(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ontology_client,
        "ask",
        lambda _q: {
            "tools_used": ["literature_review"],
            "tool_args": {"process": "хлорирование"},
            "claims": [
                {
                    "text": "Метод «chlorination»: 1 док.",
                    "kind": "review",
                    "citations": ["doc snippet"],
                }
            ],
        },
    )
    session = _create_session(client, normal_user_token_headers)
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}/messages",
        headers=normal_user_token_headers,
        json={"content": "что известно про хлорирование?"},
    )
    assert r.status_code == 200
    body = _parse_sse(r.text)
    assert body.tools_used[0] == "ontology:literature_review"
    assert "chlorination" in body.summary


def test_post_message_mode_ontology_forced(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mode=ontology не должен трогать science-knowledge-graph/hybrid_search."""
    monkeypatch.setattr(
        ontology_client,
        "ask",
        lambda _q: {
            "tools_used": ["literature_review"],
            "tool_args": {"process": "хлорирование"},
            "claims": [
                {
                    "text": "Метод «chlorination»: 1 док.",
                    "kind": "review",
                    "citations": ["doc snippet"],
                }
            ],
        },
    )
    session = _create_session(client, normal_user_token_headers)
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}/messages",
        headers=normal_user_token_headers,
        json={
            "content": "что известно про хлорирование?",
            "metadata": {"mode": "ontology"},
        },
    )
    assert r.status_code == 200
    body = _parse_sse(r.text)
    assert body.mode_used == "ontology"
    assert body.tools_used == ["ontology:literature_review"]


def test_post_message_mode_ontology_no_match(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mode=ontology с пустым ответом онтологии не должен деградировать в KG."""
    monkeypatch.setattr(
        ontology_client,
        "ask",
        lambda _q: {"tools_used": [], "tool_args": {}, "claims": []},
    )
    session = _create_session(client, normal_user_token_headers)
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}/messages",
        headers=normal_user_token_headers,
        json={"content": "steel hardness?", "metadata": {"mode": "ontology"}},
    )
    assert r.status_code == 200
    body = _parse_sse(r.text)
    assert body.mode_used == "ontology"
    assert body.tools_used == ["ontology:no_match"]


def test_post_message_mode_knowledge_graph_skips_ontology(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mode=knowledge_graph не должен обращаться к ontology_client вовсе."""

    def _boom(_q: str) -> dict[str, Any]:
        raise AssertionError("ontology_client.ask must not be called")

    monkeypatch.setattr(ontology_client, "ask", _boom)
    session = _create_session(client, normal_user_token_headers)
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}/messages",
        headers=normal_user_token_headers,
        json={
            "content": "What do we know about steel hardness?",
            "metadata": {"mode": "knowledge_graph"},
        },
    )
    assert r.status_code == 200
    body = _parse_sse(r.text)
    assert body.mode_used == "knowledge_graph"
    assert body.tools_used == ["hybrid_search"]


def test_post_message_mode_auto_reports_mode_used(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
) -> None:
    """Дефолтный auto-режим отмечает mode_used=knowledge_graph при пустой онтологии."""
    session = _create_session(client, normal_user_token_headers)
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}/messages",
        headers=normal_user_token_headers,
        json={"content": "What do we know about steel hardness?"},
    )
    assert r.status_code == 200
    body = _parse_sse(r.text)
    assert body.mode_used == "knowledge_graph"


def test_post_message_chitchat_via_ontology(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ontology_client,
        "ask",
        lambda _q: {
            "tools_used": ["chitchat"],
            "tool_args": {},
            "claims": [{"text": "Я — ассистент по базе знаний", "kind": "chat"}],
        },
    )
    session = _create_session(client, normal_user_token_headers)
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}/messages",
        headers=normal_user_token_headers,
        json={"content": "привет"},
    )
    assert r.status_code == 200
    body = _parse_sse(r.text)
    assert body.tools_used == ["ontology:chitchat"]
    assert "ассистент" in body.summary.lower()
