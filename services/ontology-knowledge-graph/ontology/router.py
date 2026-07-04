# -*- coding: utf-8 -*-
"""
FastAPI-роутер онтологии — подключение к backend одной строкой:

    from ontology.router import router as ontology_router
    app.include_router(ontology_router, prefix="/api/v1")

Ручки повторяют контракт BFF/analytics (SPEC_V5 §9, §16.1): gaps, contradictions,
compare, compare/technologies, risk-zones, coverage, subgraph, experts-by-topic,
evidence, lineage, timeline, review. Всё read-only; запись — только через
loader/extract-конвейер (read/write split из SPEC_V5).

Подключение к БД: env ONTOLOGY_DB_URL. Соединение создаётся на запрос и
закрывается после ответа (без пула — при интеграции backend может подменить
зависимость get_store на свой пул).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from . import query as q
from .store import Store

router = APIRouter(tags=["ontology"])


@router.get("/health")
def health() -> dict:
    """Без обращения к БД — для docker healthcheck и клиента backend'а."""
    return {"status": "ok", "name": "ontology-knowledge-graph"}


def get_store():
    store = Store.open()
    try:
        yield store
    finally:
        store.close()


# ── analytics ────────────────────────────────────────────────────────────

@router.get("/analytics/gaps")
def gaps(min_sources: int = 3, store: Store = Depends(get_store)) -> dict:
    return q.find_gaps(store, min_sources)


@router.get("/analytics/contradictions")
def contradictions(store: Store = Depends(get_store)) -> list[dict]:
    return q.find_contradictions(store)


@router.get("/analytics/compare")
def compare(process: str, store: Store = Depends(get_store)) -> dict:
    return q.compare_practice(store, process)


@router.get("/analytics/compare/technologies")
def compare_technologies(processes: str = Query(..., description="через запятую"),
                         store: Store = Depends(get_store)) -> list[dict]:
    return q.compare_technologies(store, [p.strip() for p in processes.split(",")])


@router.get("/analytics/risk-zones")
def risk(min_sources: int = 3, store: Store = Depends(get_store)) -> list[dict]:
    return q.risk_zones(store, min_sources)


@router.get("/analytics/coverage")
def coverage(store: Store = Depends(get_store)) -> dict:
    return q.coverage(store)


@router.get("/analytics/profile")
def profile(quantity_kind: str, material: Optional[str] = None,
            process: Optional[str] = None,
            store: Store = Depends(get_store)) -> dict:
    return q.evidence_profile(store, quantity_kind, material, process)


@router.get("/analytics/review")
def review(process: Optional[str] = None, store: Store = Depends(get_store)) -> dict:
    return q.literature_review(store, process)


# ── graph ────────────────────────────────────────────────────────────────

@router.get("/graph/subgraph/{entity}")
def subgraph(entity: str, depth: int = 1, store: Store = Depends(get_store)) -> dict:
    return q.get_subgraph(store, entity, depth)


class TopicIn(BaseModel):
    topic: str
    limit: int = 5


@router.post("/graph/experts-by-topic")
def experts(body: TopicIn, store: Store = Depends(get_store)) -> list[dict]:
    return q.find_experts_by_topic(store, body.topic, body.limit)


@router.get("/graph/lineage/{entity}")
def lineage(entity: str, store: Store = Depends(get_store)) -> list[dict]:
    return q.lineage(store, entity)


# ── evidence / timeline ──────────────────────────────────────────────────

@router.get("/evidence")
def evidence(material: Optional[str] = None, process: Optional[str] = None,
             quantity_kind: Optional[str] = None,
             value_op: Optional[str] = Query(None, pattern="^(<=|>=)$"),
             value: Optional[float] = None, year_from: Optional[int] = None,
             country: Optional[str] = None,
             store: Store = Depends(get_store)) -> dict:
    ev = q.evidence(store, material, process, quantity_kind,
                    value_op, value, year_from, country)
    return ev.model_dump()


@router.get("/timeline")
def timeline(material: Optional[str] = None, process: Optional[str] = None,
             store: Store = Depends(get_store)) -> list[dict]:
    return q.timeline(store, material, process)
