"""Синхронный HTTP-клиент к `ontology-knowledge-graph` — отдельному
internal-only сервису (типизированная онтология на Postgres: провенанс-цитаты,
Comparability Gate, evidence/gaps/contradictions/experts; см. ontology/README.md),
не части этого uv-workspace.

Все функции глушат `httpx.HTTPError` и возвращают `None`/`False` — сервис
опционален (как и science-knowledge-graph), недоступность не должна валить
chat/graph/analytics, только деградировать до пустого результата.

`trust_env=False` на каждом вызове: internal-only вызов по docker-сети,
системный HTTP(S)_PROXY подхватывать не нужно.
"""

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(15.0, connect=3.0)
# /ask может ходить в LLM-интент на стороне сервиса; холодный старт модели
# заметно дольше обычного запроса — как _RAG_TIMEOUT у science_kg_client.
_ASK_TIMEOUT = httpx.Timeout(90.0, connect=3.0)


def available() -> bool:
    try:
        resp = httpx.get(
            f"{settings.ONTOLOGY_KG_URL}/api/v1/health",
            timeout=_TIMEOUT,
            trust_env=False,
        )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def ask(question: str) -> dict[str, Any] | None:
    """POST /api/v1/ask — NL-вопрос → интент → тул → structured claims с
    дословными цитатами. Shape: {question, tools_used[], tool_args,
    claims:[{text, kind, confidence?, n_sources?, citations[]}]}.
    Пустые claims = онтология не нашла данных под вопрос."""
    try:
        resp = httpx.post(
            f"{settings.ONTOLOGY_KG_URL}/api/v1/ask",
            json={"question": question},
            timeout=_ASK_TIMEOUT,
            trust_env=False,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data
    except httpx.HTTPError as exc:
        logger.warning("ontology-kg ask failed: %s", exc)
        return None


def invoke(tool: str, args: dict[str, Any] | None = None) -> Any | None:
    """POST /invoke — универсальный вызов тула онтологии по manifest-контракту.
    Тулы: evidence, evidence_profile, find_gaps, find_contradictions,
    compare_practice, compare_technologies, find_experts_by_topic, get_subgraph,
    lineage, timeline, literature_review, coverage."""
    try:
        resp = httpx.post(
            f"{settings.ONTOLOGY_KG_URL}/invoke",
            json={"tool": tool, "args": args or {}},
            timeout=_TIMEOUT,
            trust_env=False,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        if not data.get("ok"):
            logger.warning("ontology-kg invoke(%s) failed: %s", tool, data.get("error"))
            return None
        return data.get("result")
    except httpx.HTTPError as exc:
        logger.warning("ontology-kg invoke(%s) failed: %s", tool, exc)
        return None


def evidence(
    *,
    material: str | None = None,
    process: str | None = None,
    quantity_kind: str | None = None,
    value_op: str | None = None,
    value: float | None = None,
    year_from: int | None = None,
    country: str | None = None,
) -> dict[str, Any] | None:
    """Hero-ответ «что делали по X и какой результат Z» с цитатами.
    Shape: {answer, experiments[], n_experiments, n_docs, labs[], confidence,
    agreement_flag, citations[{doc_id, locator, snippet, ...}], gap_note}."""
    args = {
        k: v
        for k, v in {
            "material": material,
            "process": process,
            "quantity_kind": quantity_kind,
            "value_op": value_op,
            "value": value,
            "year_from": year_from,
            "country": country,
        }.items()
        if v is not None
    }
    return invoke("evidence", args)


def evidence_profile(
    quantity_kind: str,
    *,
    material: str | None = None,
    process: str | None = None,
) -> dict[str, Any] | None:
    """«Пространство решений» по величине: конверт min-max, медиана, точки с
    надёжностью источников, выбросы; только сопоставимые точки (Gate)."""
    args: dict[str, Any] = {"quantity_kind": quantity_kind}
    if material:
        args["material"] = material
    if process:
        args["process"] = process
    return invoke("evidence_profile", args)


def find_gaps(min_sources: int = 3) -> dict[str, Any] | None:
    """Пробелы: пустые ячейки материал×величина, гео-эксклюзивные процессы,
    темы с малым числом источников."""
    return invoke("find_gaps", {"min_sources": min_sources})


def find_contradictions() -> list[dict[str, Any]] | None:
    """Зоны расхождения (severity, две стороны с цитатами и надёжностью) —
    только среди сопоставимых данных из разных документов."""
    return invoke("find_contradictions")


def compare_practice(process: str) -> dict[str, Any] | None:
    """Отечественная vs зарубежная практика по процессу."""
    return invoke("compare_practice", {"process": process})


def find_experts_by_topic(topic: str, limit: int = 5) -> list[dict[str, Any]] | None:
    """Лаборатории/эксперты по теме (экспертиза + число экспериментов)."""
    return invoke("find_experts_by_topic", {"topic": topic, "limit": limit})


def get_subgraph(entity: str, depth: int = 1) -> dict[str, Any] | None:
    """Окрестность узла для граф-визуализации. Shape: {nodes[{id,label,ntype}],
    edges[{src,dst,predicate,attrs}]}."""
    return invoke("get_subgraph", {"entity": entity, "depth": depth})
