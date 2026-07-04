# -*- coding: utf-8 -*-
"""Роутинг mock-агента: chitchat, ретрив-фолбэк, evidence со слотами.

LLM-роутер здесь принудительно выключен (детерминизм, без сети) — проверяется
keyword-фолбэк detect_intent. Логика _from_llm покрывается отдельно.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontology.loader import load_batch, seed_registries
from ontology.mocks import agent as agent_mod
from ontology.mocks.agent import _from_llm, _is_chitchat, answer, detect_intent
from ontology.store import Store

SEED = Path(__file__).parent.parent / "seed" / "norilsk_pgm.json"


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    # маршрутизация и синтез — без обращения к LLM (детерминизм, оффлайн):
    # keyword-роутер + сырые пассажи вместо синтезированного ответа.
    monkeypatch.setattr(agent_mod, "_USE_LLM_INTENT", False)
    monkeypatch.setattr(agent_mod, "_USE_SYNTH", False)


@pytest.fixture(scope="module")
def store():
    s = Store.open()
    s.reset()
    seed_registries(s)
    load_batch(s, json.loads(SEED.read_text(encoding="utf-8")))
    yield s
    s.close()


@pytest.mark.parametrize(
    "text",
    ["привет", "hi", "супер", "ok", "ок",
     # пунктуация, прилипшая к первому слову, не должна ломать распознавание
     "Привет! Что ты умеешь?", "Здравствуйте.", "Что умеешь?", "как дела?"],
)
def test_chitchat_detected(text: str) -> None:
    assert _is_chitchat(text.lower()) is True


@pytest.mark.parametrize(
    "text",
    ["Показатели извлечения при хлорировании?", "Какие методы обессоливания?",
     "Пороговое напряжение в массиве", "Химический состав штейна"],
)
def test_domain_questions_not_chitchat(text: str) -> None:
    # первый токен, начинающийся на короткий стем («пока»/«хай»), не должен
    # ложно распознаваться как приветствие
    assert _is_chitchat(text.lower()) is False


def test_greeting_with_punctuation_routes_to_chitchat(store: Store) -> None:
    # регресс: «Привет! Что ты умеешь?» должно идти в chitchat, а не в поиск фактов
    result = answer(store, "Привет! Что ты умеешь?")
    assert result["tools_used"] == ["chitchat"]
    assert result["claims"]


def test_slotless_question_routes_to_retrieval(store: Store) -> None:
    # без числовой величины вопрос уходит в ретрив пассажей (не пустой no_match)
    tool, args = detect_intent(store, "random unrelated question xyz")
    assert tool == "search_passages"
    assert "query" in args


def test_methods_question_routes_to_retrieval(store: Store) -> None:
    tool, args = detect_intent(store, "какие методы аффинажа существуют?")
    assert tool == "search_passages"


def test_evidence_requires_slots(store: Store) -> None:
    tool, args = detect_intent(store, "какое извлечение даёт хлорирование?")
    assert tool == "evidence"
    assert args.get("process")


def test_answer_retrieval_reports_tool(store: Store) -> None:
    result = answer(store, "random unrelated question xyz")
    assert result["tools_used"] == ["search_passages"]


def test_answer_chitchat_not_evidence(store: Store) -> None:
    result = answer(store, "супер")
    assert result["tools_used"] == ["chitchat"]
    assert result["claims"]


def test_answer_literature_review(store: Store) -> None:
    result = answer(store, "что известно про хлорирование?")
    assert result["tools_used"] == ["literature_review"]
    assert result["claims"]


def test_from_llm_maps_intents(store: Store) -> None:
    # чистое отображение intent → (tool, args), без сети
    assert _from_llm(store, "q", {"intent": "chitchat"})[0] == "chitchat"
    tool, args = _from_llm(store, "q", {
        "intent": "evidence", "quantity_kind": "извлечение",
        "process": "хлорирование", "value_op": ">=", "value": "90"})
    assert tool == "evidence" and args["quantity_kind"] == "извлечение"
    assert args.get("value_op") == ">=" and args.get("value") == 90.0
    tool, args = _from_llm(store, "какие методы?", {
        "intent": "search_passages", "process": "флотация"})
    assert tool == "search_passages" and args["query"] == "какие методы?"
    # evidence без величины деградирует в ретрив
    assert _from_llm(store, "q", {"intent": "evidence"})[0] == "search_passages"
