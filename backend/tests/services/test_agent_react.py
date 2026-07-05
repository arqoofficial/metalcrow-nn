"""Юнит-тесты ReAct-агента: сужение каталога по режиму, инварианты провенанса
grounder'а и мягкий разбор JSON — всё без сети, LLM и БД (чистые функции +
монкипатч клиентов)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.schemas.chat import (
    ChatMessageMetadata,
    ChatMessageRequest,
    ChatMode,
    ClaimConfidence,
    ClaimKind,
)
from app.services import ontology_client
from app.services.agent import llm, react, tools


def test_catalog_for_mode_scopes_by_source(monkeypatch: pytest.MonkeyPatch) -> None:
    # Онто-тулы авто-регистрируются из /manifest — подменяем его, без сети.
    monkeypatch.setattr(tools, "_ontology_cache", None)
    monkeypatch.setattr(
        tools.ontology_client,
        "manifest",
        lambda: {"tools": {"evidence": {"description": "d", "properties": {}}}},
    )
    onto = {t.source for t in tools.catalog_for_mode(ChatMode.ONTOLOGY)}
    kg = {t.source for t in tools.catalog_for_mode(ChatMode.KNOWLEDGE_GRAPH)}
    both = {t.source for t in tools.catalog_for_mode(ChatMode.AUTO)}
    assert onto == {tools.ONTOLOGY}
    assert kg == {tools.KG}
    assert both == {tools.ONTOLOGY, tools.KG}
    onto_names = {t.name for t in tools.catalog_for_mode(ChatMode.ONTOLOGY)}
    assert "ontology_ask" in onto_names  # high-level тул
    assert "evidence" in onto_names  # авто-регистрация из manifest


def test_ground_resolves_refs_and_embeds_source() -> None:
    exp = str(uuid.uuid4())
    claims = react._ground(
        [
            {
                "text": "Плотность тока 250 A/m2.",
                "confidence": "high",
                "kind": "fact",
                "evidence_ids": ["E1", "S1"],
            }
        ],
        pool_exp={"E1": exp},
        pool_src={"S1": "Отчёт X, с.3"},
    )
    assert len(claims) == 1
    claim = claims[0]
    assert [str(x) for x in claim.experiment_ids] == [exp]
    assert "— источник: «Отчёт X, с.3»" in claim.text
    assert claim.confidence == ClaimConfidence.HIGH


def test_ground_downgrades_unsourced_fact() -> None:
    # Пустой пул — claim не может сослаться ни на что → confidence принудительно low
    # и строка-источник не подставляется (инвариант анти-галлюцинаций).
    claims = react._ground(
        [
            {
                "text": "Безосновательное утверждение.",
                "confidence": "high",
                "kind": "fact",
                "evidence_ids": [],
            }
        ],
        pool_exp={},
        pool_src={},
    )
    assert claims[0].confidence == ClaimConfidence.LOW
    assert "— источник:" not in claims[0].text


def test_ground_downgrades_when_refs_invalid() -> None:
    exp = str(uuid.uuid4())
    claims = react._ground(
        [
            {
                "text": "Ответ.",
                "confidence": "medium",
                "kind": "fact",
                "evidence_ids": ["BOGUS"],
            }
        ],
        pool_exp={"E1": exp},
        pool_src={"S1": "Док Y"},
    )
    # Невалидные evidence_ids — не подставляем весь пул (анти-галлюцинации).
    assert claims[0].experiment_ids == []
    assert claims[0].confidence == ClaimConfidence.LOW
    assert "— источник:" not in claims[0].text
    assert claims[0].sources == []


def test_mode_used_reflects_sources() -> None:
    assert react._mode_used({tools.ONTOLOGY, tools.KG}) == "both"
    assert react._mode_used({tools.ONTOLOGY}) == "ontology"
    assert react._mode_used({tools.KG}) == "knowledge_graph"


def test_try_parse_strips_markdown_fence() -> None:
    assert llm._try_parse('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm._try_parse('текст до {"a": 2} текст после') == {"a": 2}
    assert llm._try_parse("совсем не json") is None


def test_run_agent_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Планировщик зовёт ontology_ask и завершает; синтез даёт один claim со
    ссылкой на источник из наблюдения. Ни сети, ни БД."""
    monkeypatch.setattr(tools, "_ontology_cache", None)
    monkeypatch.setattr(tools.ontology_client, "manifest", lambda: {"tools": {}})
    monkeypatch.setattr(
        ontology_client,
        "ask",
        lambda _q, **_kw: {
            "claims": [
                {"text": "медь при 60C", "citations": ["Отчёт Z с.5: плотность тока 300"]}
            ]
        },
    )
    scripted: list[dict[str, Any]] = [
        {
            "thought": "спрошу онтологию",
            "tool": "ontology_ask",
            "args": {"question": "про медь"},
            "done": False,
        },
        {"thought": "достаточно", "tool": None, "args": {}, "done": True},
        {
            "summary": "Готово.",
            "claims": [
                {
                    "text": "Медь при 60C.",
                    "confidence": "high",
                    "kind": "fact",
                    "evidence_ids": ["S1"],
                }
            ],
        },
    ]
    calls = {"i": 0}

    def fake_complete_json(*_a: Any, **_k: Any) -> dict[str, Any]:
        result = scripted[calls["i"]]
        calls["i"] += 1
        return result

    monkeypatch.setattr(react, "complete_json", fake_complete_json)

    request = ChatMessageRequest(
        content="что известно про медь?",
        metadata=ChatMessageMetadata(mode=ChatMode.ONTOLOGY),
    )
    session_id = uuid.uuid4()
    # session не используется онтологическим тулом (HTTP-путь) — передаём None.
    response = react.run_agent(session=None, chat_session_id=session_id, request=request)  # type: ignore[arg-type]

    assert response.session_id == session_id
    assert response.mode_used == "ontology"
    assert response.tools_used == ["ontology_ask"]
    assert len(response.claims) == 1
    assert response.claims[0].kind == ClaimKind.FACT
    assert "— источник:" in response.claims[0].text
