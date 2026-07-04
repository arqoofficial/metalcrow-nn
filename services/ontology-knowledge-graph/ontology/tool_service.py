# -*- coding: utf-8 -*-
"""
Тул-сервис онтологии по единому контракту tool plane (SPEC_V5 §3):

    GET  /health   → {status, name, version}
    GET  /manifest → описание тулов для агента (JSON Schema аргументов)
    POST /invoke   → {"tool": "...", "args": {...}} → {"ok": true, "result": ...}

Запуск standalone:  uvicorn ontology.tool_service:app --port 8021
Либо те же тулы доступны как Python-функции (in-process fallback агента):
ontology.tool_service.TOOLS["find_gaps"]["fn"](store, **args).
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from . import query as q
from .router import router
from .store import Store

NAME, VERSION = "svc-ontology", "1.0"


def _s(desc: str, **props) -> dict:
    return {"description": desc, "type": "object", "properties": props}

TOOLS: dict[str, dict] = {
    "evidence": {
        "fn": lambda store, **a: q.evidence(store, **a).model_dump(),
        "schema": _s("Что делали по материалу/процессу и какой результат по величине; "
                     "поддерживает числовые фильтры value_op(<=|>=)+value",
                     material={"type": "string"}, process={"type": "string"},
                     quantity_kind={"type": "string"}, value_op={"type": "string"},
                     value={"type": "number"}, year_from={"type": "integer"},
                     country={"type": "string"})},
    "evidence_profile": {
        "fn": lambda store, **a: q.evidence_profile(store, **a),
        "schema": _s("Пространство решений по величине: конверт min-max, медиана, "
                     "точки с надёжностью источников, выбросы; агрегируются только "
                     "сопоставимые точки",
                     quantity_kind={"type": "string"}, material={"type": "string"},
                     process={"type": "string"})},
    "find_gaps": {
        "fn": lambda store, **a: q.find_gaps(store, **a),
        "schema": _s("Пробелы: пустые ячейки материал×величина, практика только "
                     "RU/только зарубежная, темы с малым числом источников",
                     min_sources={"type": "integer", "default": 3})},
    "find_contradictions": {
        "fn": lambda store, **a: q.find_contradictions(store, **a),
        "schema": _s("Противоречия среди СОПОСТАВИМЫХ данных из разных документов "
                     "(измерения и выводы)", rel_delta={"type": "number", "default": 0.3})},
    "compare_practice": {
        "fn": lambda store, **a: q.compare_practice(store, **a),
        "schema": _s("Отечественная vs зарубежная практика по процессу",
                     process={"type": "string"})},
    "compare_technologies": {
        "fn": lambda store, **a: q.compare_technologies(store, **a),
        "schema": _s("Таблица метод×параметр×значение×источник по списку методов",
                     processes={"type": "array", "items": {"type": "string"}})},
    "find_experts_by_topic": {
        "fn": lambda store, **a: q.find_experts_by_topic(store, **a),
        "schema": _s("Лаборатории/эксперты по теме", topic={"type": "string"},
                     limit={"type": "integer", "default": 5})},
    "get_subgraph": {
        "fn": lambda store, **a: q.get_subgraph(store, **a),
        "schema": _s("Окрестность узла для граф-визуализации",
                     entity={"type": "string"}, depth={"type": "integer", "default": 1})},
    "lineage": {
        "fn": lambda store, **a: q.lineage(store, **a),
        "schema": _s("Цепочка переделов derived_from («история решений»)",
                     entity={"type": "string"})},
    "timeline": {
        "fn": lambda store, **a: q.timeline(store, **a),
        "schema": _s("Кто/когда/что по материалу или процессу",
                     material={"type": "string"}, process={"type": "string"})},
    "literature_review": {
        "fn": lambda store, **a: q.literature_review(store, **a),
        "schema": _s("Секции литобзора: by_method/by_geo/by_year/consensus/"
                     "disagreements/claims", process={"type": "string"})},
    "coverage": {
        "fn": lambda store, **a: q.coverage(store),
        "schema": _s("Покрытие корпуса + зоны риска (дашборд)")},
    "search_passages": {
        "fn": lambda store, **a: q.search_passages(store, **a),
        "schema": _s("Полнотекстовый ретрив релевантных пассажей корпуса (выводы + "
                     "измерения) с обязательной ссылкой на документ-источник; "
                     "фолбэк для вопросов «какие методы/способы/технические решения» и "
                     "любых открытых запросов, где нет одной числовой величины",
                     query={"type": "string"}, process={"type": "string"},
                     limit={"type": "integer", "default": 8})},
}

app = FastAPI(title=NAME, version=VERSION)
app.include_router(router, prefix="/api/v1")     # BFF-ручки тоже доступны


@app.on_event("startup")
def _build_hybrid_index() -> None:
    """Собрать гибридный индекс пассажей в фоне (не блокирует healthcheck).
    До готовности эмбеддингов ретрив работает лексически."""
    try:
        from .hybrid_index import start_background_build
        start_background_build()
    except Exception:
        pass


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "name": NAME, "version": VERSION}


@app.get("/manifest")
def manifest() -> dict:
    return {"name": NAME, "version": VERSION,
            "degraded": "при недоступности БД возвращает 503; LLM не используется",
            "tools": {k: v["schema"] for k, v in TOOLS.items()}}


class Ask(BaseModel):
    question: str


@app.post("/api/v1/ask")
def ask(body: Ask) -> dict:
    """Вопрос на естественном языке → интент → тул → structured claims с
    цитатами. Форма ответа: {question, tools_used, tool_args, claims:[{text,
    kind, confidence?, n_sources?, citations[]}]}. Пустые claims = онтология
    не нашла подходящих данных (вызывающий деградирует на свой контур)."""
    from .mocks.agent import answer as agent_answer   # lazy: избегаем цикла импорта
    store = Store.open()
    try:
        result = agent_answer(store, body.question)
        result.pop("raw", None)                       # сырой результат тула не гоняем по сети
        return result
    finally:
        store.close()


class Invoke(BaseModel):
    tool: str
    args: dict = {}


@app.post("/invoke")
def invoke(body: Invoke) -> dict:
    if body.tool not in TOOLS:
        return {"ok": False, "error": f"нет тула {body.tool}",
                "available": sorted(TOOLS)}
    store = Store.open()
    try:
        return {"ok": True, "result": TOOLS[body.tool]["fn"](store, **body.args)}
    except TypeError as e:
        return {"ok": False, "error": f"неверные аргументы: {e}"}
    finally:
        store.close()
