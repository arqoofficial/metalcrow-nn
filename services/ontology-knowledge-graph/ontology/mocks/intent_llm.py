# -*- coding: utf-8 -*-
"""LLM-классификатор интента (gpt-oss-120b) над реестром тулов онтологии.

Заменяет хрупкий keyword-роутер: по вопросу выбирает один тул и извлекает
слоты. Эндпоинт — тот же OpenAI-совместимый (LLM_BASE_URL/LLM_API_KEY);
модель — LLM_INTENT_MODEL (по умолчанию Gpt-oss-120b), reasoning_effort=low.

Любая ошибка/таймаут → None: вызывающий (mocks.agent) откатывается на
keyword-роутер. LLM здесь только маршрутизирует — числа и цитаты всегда из БД.
"""
from __future__ import annotations

import json
import os
import threading

from ..extract.llm import _read_base_url, _read_env_key, _dotenv

INTENTS = [
    "evidence", "evidence_profile", "search_passages", "find_contradictions",
    "find_gaps", "find_experts_by_topic", "compare_practice",
    "compare_technologies", "lineage", "timeline", "literature_review",
    "coverage", "chitchat",
]

_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "process": {"type": "string", "description": "процесс/метод из вопроса, напр. хлорирование, обессоливание; иначе ''"},
        "material": {"type": "string", "description": "материал/вещество из вопроса; иначе ''"},
        "quantity_kind": {"type": "string", "description": "измеряемая величина, напр. извлечение, сухой остаток, плотность тока; '' если вопрос не про одно число"},
        "topic": {"type": "string", "description": "тема для экспертов/сравнения; иначе ''"},
        "value_op": {"type": "string", "enum": ["<=", ">=", ""]},
        "value": {"type": "string", "description": "число из ограничения (только цифры) или ''"},
    },
    "required": ["intent", "process", "material", "quantity_kind", "topic",
                 "value_op", "value"],
    "additionalProperties": False,
}

_PROMPT = (
    "Ты — маршрутизатор вопросов к базе знаний по горно-металлургическому R&D "
    "(Ni/Cu/МПГ: обогащение, пиро- и гидрометаллургия, вода, геомеханика).\n"
    "Выбери РОВНО ОДИН intent и извлеки слоты (по-русски, как в вопросе).\n"
    "Интенты:\n"
    "- evidence — спрашивают ОДНО числовое значение величины для материала/процесса "
    "(«какое извлечение даёт хлорирование»).\n"
    "- evidence_profile — диапазон/оптимум/разброс величины («в каком диапазоне», «оптимальная плотность тока»).\n"
    "- search_passages — ПО УМОЛЧАНИЮ: спрашивают методы/способы/технические решения/что известно/"
    "как делают, без одной конкретной величины. Ставь его, когда сомневаешься.\n"
    "- find_contradictions — противоречия/расхождения между источниками.\n"
    "- find_gaps — пробелы, что не изучено/мало данных.\n"
    "- find_experts_by_topic — кто/какие лаборатории занимались темой (topic).\n"
    "- compare_practice — отечественная vs зарубежная практика по процессу.\n"
    "- compare_technologies — таблица сравнения нескольких методов по параметрам.\n"
    "- lineage — цепочка переделов, из чего получают материал.\n"
    "- timeline — кто/когда/что по материалу/процессу во времени.\n"
    "- literature_review — литературный обзор по процессу.\n"
    "- coverage — статистика/покрытие базы.\n"
    "- chitchat — ТОЛЬКО приветствие/благодарность/вопрос о твоих возможностях "
    "(«привет», «что умеешь»). Фактический вопрос вне домена (акции, погода, общие "
    "справочные факты) — это НЕ chitchat, ставь search_passages.\n"
    "Верни строго JSON по схеме. Пустые слоты — ''.\n\nВОПРОС: "
)

_lock = threading.Lock()
_client = None
_model: str | None = None


def _ensure_client():
    global _client, _model
    if _client is not None:
        return _client
    with _lock:
        if _client is not None:
            return _client
        from openai import OpenAI
        _client = OpenAI(base_url=_read_base_url(), api_key=_read_env_key(),
                         timeout=25.0, max_retries=1)
        want = (_dotenv("LLM_INTENT_MODEL") or os.environ.get("LLM_INTENT_MODEL")
                or "Openai/Gpt-oss-120b")
        try:
            available = {m.id for m in _client.models.list()}
            _model = want if want in available else (
                "Openai/Gpt-oss-120b" if "Openai/Gpt-oss-120b" in available
                else next(iter(available), want))
        except Exception:
            _model = want
        return _client


def classify(question: str) -> dict | None:
    """Вопрос → {intent, слоты} или None при ошибке/таймауте."""
    try:
        client = _ensure_client()
        kwargs: dict = dict(
            model=_model, temperature=0, max_tokens=400,
            response_format={"type": "json_schema", "json_schema": {
                "name": "intent", "schema": _SCHEMA, "strict": True}},
            messages=[{"role": "user", "content": _PROMPT + question}])
        if _model and "Gpt-oss" in _model:
            kwargs["reasoning_effort"] = "low"
        r = client.chat.completions.create(**kwargs)
        data = json.loads(r.choices[0].message.content or "{}")
        if data.get("intent") in INTENTS:
            return data
    except Exception:
        return None
    return None
