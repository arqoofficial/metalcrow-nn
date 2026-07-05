"""Каталог тулов ретрива для ReAct-агента (SPEC_V3 Приложение C / V4-5 tool plane).

Онтологические тулы регистрируются АВТОМАТИЧЕСКИ из `/manifest` онтологического
сервиса (имя + JSON-Schema аргументов) и диспатчатся через `/invoke` — их список не
хардкодится и переживает переделку онтологии/набора тулов. KG-плоскость
(hybrid_search по Postgres, graph_rag по science-kg) обёрнута напрямую — это не
manifest-тулы. Плюс high-level `ontology_ask` (LLM-роутер интентов на стороне
онтологии) для широких вопросов, где агенту трудно сформулировать низкоуровневые
аргументы.

Каждый тул → Observation: компактный текст для LLM + провенанс (experiment_ids /
строки-источники). Провенанс из онто-результатов извлекается ГЕНЕРИЧЕСКИ
(`_harvest_sources`) по общим ключам (citations/passages: doc/doc_id/locator/
snippet), поэтому не зависит от точной формы ответа конкретного тула.

Режим чата (`ChatMode`) сужает каталог: ontology / knowledge_graph / auto (оба).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from app.schemas.chat import ChatMode, ChatSource
from app.schemas.search import SearchRequest
from app.services import ontology_client, science_kg_client
from app.services import search as search_service

logger = logging.getLogger(__name__)

ONTOLOGY = "ontology"
KG = "kg"


@dataclass
class Observation:
    """Результат одного вызова тула: текст для LLM + машиночитаемый провенанс.

    `ok=False` — тул отработал, но данных нет / сервис недоступен: такое наблюдение
    не «продуктивно» и само по себе не удерживает ответ от отката на водопад."""

    text: str
    experiment_ids: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # строки-цитаты для текста
    chat_sources: list[ChatSource] = field(default_factory=list)  # структурные чипы
    ok: bool = True


ToolRun = Callable[[Session, dict[str, Any]], Observation]


@dataclass
class ToolSpec:
    name: str
    source: str  # ONTOLOGY | KG
    description: str
    parameters: dict[str, Any]  # JSON Schema properties тула
    run: ToolRun


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _cite(doc: str, locator: str, snippet: str) -> str:
    """Единый формат строки-провенанса: «Документ (локатор): дословная цитата»."""
    head = doc if not locator else f"{doc} ({locator})"
    return _clip(f"{head}: {_clip(snippet, 320)}".strip(), 420)


def _basic_chat_source(doc: str) -> ChatSource:
    """Базовый источник-чип из имени документа — без wiki-ссылки (онто-доки не
    лежат в parser SHARED). KG-doc_id резолвятся в кликабельные чипы отдельно."""
    name = doc.rsplit("/", 1)[-1].strip() or doc
    return ChatSource(doc_id=doc, filename=name)


def _dedup_chat_sources(items: list[ChatSource]) -> list[ChatSource]:
    seen: set[str] = set()
    out: list[ChatSource] = []
    for cs in items:
        key = cs.source_path or cs.filename or cs.doc_id
        if key and key not in seen:
            seen.add(key)
            out.append(cs)
    return out


# ── generic извлечение провенанса из произвольного онто-результата ─────────────
def _harvest_sources(
    obj: Any, out: list[str], chat_out: list[ChatSource], depth: int = 0
) -> None:
    if depth > 4 or len(out) >= 8:
        return
    if isinstance(obj, dict):
        snippet = obj.get("snippet") or obj.get("text")
        doc = obj.get("doc") or obj.get("doc_id") or obj.get("document")
        if snippet and doc:
            out.append(_cite(str(doc), str(obj.get("locator") or ""), str(snippet)))
            chat_out.append(_basic_chat_source(str(doc)))
        for value in obj.values():
            _harvest_sources(value, out, chat_out, depth + 1)
    elif isinstance(obj, list):
        for value in obj[:8]:
            _harvest_sources(value, out, chat_out, depth + 1)


def _observe_generic(label: str, result: Any) -> Observation:
    if not result:
        return Observation(text=f"{label}: данных нет или сервис недоступен.", ok=False)
    sources: list[str] = []
    chat_sources: list[ChatSource] = []
    _harvest_sources(result, sources, chat_sources)
    seen: set[str] = set()
    sources = [s for s in sources if not (s in seen or seen.add(s))]
    body = _clip(f"{label}: {json.dumps(result, ensure_ascii=False)}", 1600)
    return Observation(
        text=body,
        sources=sources[:6],
        chat_sources=_dedup_chat_sources(chat_sources)[:6],
    )


# ── KG-плоскость (не manifest-тулы) ───────────────────────────────────────────
def _run_hybrid_search(session: Session, args: dict[str, Any]) -> Observation:
    query = str(args.get("query") or "").strip()
    if not query:
        return Observation(text="hybrid_search: пустой запрос.", ok=False)
    top_k = min(int(args.get("top_k") or 5), 10)
    resp = search_service.hybrid_search(session, SearchRequest(query=query, top_k=top_k))
    if not resp.results:
        return Observation(text=f"hybrid_search: по «{query}» ничего не найдено.", ok=False)
    exp_ids: list[str] = []
    sources: list[str] = []
    chat_sources: list[ChatSource] = []
    lines: list[str] = []
    for item in resp.results[:8]:
        exp_ids.append(str(item.experiment_id))
        loc: str | None = None
        src = item.source
        if src and (src.document or src.page):
            loc = src.document or "документ"
            if src.page:
                loc = f"{loc}, с.{src.page}"
            sources.append(loc)
            if src.document:
                chat_sources.append(_basic_chat_source(src.document))
        value = (
            f"{item.value} {item.unit or ''}".strip() if item.value is not None else ""
        )
        lines.append(
            f"- [{item.experiment_id}] {item.material or '?'} / "
            f"{item.property or '?'} {value} ({loc or 'источник н/д'})"
        )
    text = f"hybrid_search: всего {resp.total}, топ:\n" + "\n".join(lines)
    return Observation(
        text=_clip(text, 1500),
        experiment_ids=exp_ids,
        sources=sources,
        chat_sources=_dedup_chat_sources(chat_sources),
    )


def _run_graph_rag(_session: Session, args: dict[str, Any]) -> Observation:
    question = str(args.get("question") or args.get("query") or "").strip()
    if not question:
        return Observation(text="graph_rag: пустой вопрос.", ok=False)
    result = science_kg_client.rag_query(question)
    if not result or not result.get("answer"):
        return Observation(text="graph_rag: сервис недоступен или ответа нет.", ok=False)
    raw = [str(s) for s in (result.get("sources") or [])[:5] if s]
    sources = [_clip(s, 160) for s in raw]
    # science-kg doc_id-ы резолвятся в кликабельные чипы с wiki-ссылкой ровно тем же
    # путём, что и водопад (chat._resolve_chat_sources). Поздний импорт — цикла нет.
    from app.services.chat import _resolve_chat_sources

    return Observation(
        text=_clip(f"graph_rag: {result['answer']}", 1200),
        sources=sources,
        chat_sources=_resolve_chat_sources(raw),
        ok=bool(sources),
    )


# ── high-level онто-роутер (готовый интеллектуальный ретрив) ───────────────────
def _run_ontology_ask(_session: Session, args: dict[str, Any]) -> Observation:
    question = str(args.get("question") or args.get("query") or "").strip()
    if not question:
        return Observation(text="ontology_ask: пустой вопрос.", ok=False)
    # synth=False: агент синтезирует финал сам поверх claims — внутренний синтез
    # онтологии был бы лишним LLM-вызовом (его `answer` здесь не используется).
    result = ontology_client.ask(question, synth=False)
    claims = (result or {}).get("claims") or []
    if not claims:
        return Observation(
            text="ontology_ask: онтология не нашла структурированного ответа.", ok=False
        )
    sources: list[str] = []
    lines: list[str] = []
    for claim in claims[:6]:
        lines.append(f"- {_clip(str(claim.get('text') or ''), 200)}")
        for cite in (claim.get("citations") or [])[:2]:
            if cite:
                sources.append(_clip(str(cite), 320))
    return Observation(
        text=_clip("ontology_ask:\n" + "\n".join(lines), 1600), sources=sources[:6]
    )


# ── авто-регистрация онто-тулов из /manifest ──────────────────────────────────
def _make_ontology_tool(name: str, schema: dict[str, Any]) -> ToolSpec:
    def run(_session: Session, args: dict[str, Any], _name: str = name) -> Observation:
        return _observe_generic(
            f"ontology.{_name}", ontology_client.invoke(_name, args or {})
        )

    return ToolSpec(
        name=name,
        source=ONTOLOGY,
        description=str(schema.get("description") or name),
        parameters=schema.get("properties") or {},
        run=run,
    )


# Онто-тулы из /manifest, которые НЕ кладём в меню агента: это UI/дашборд-эндпоинты
# (граф-визуализация, покрытие корпуса), а не ответ на доменный вопрос. Они остаются
# доступны как /invoke, просто не засоряют маршрутизацию LLM (меньше тулов → точнее
# выбор). Не хардкод обёрток — только фильтр меню поверх авто-регистрации.
_ONTOLOGY_ENDPOINT_ONLY = {"get_subgraph", "coverage"}

_ontology_cache: list[ToolSpec] | None = None


def _ontology_tools() -> list[ToolSpec]:
    """Онто-тулы: high-level `ontology_ask` + тулы из `/manifest` (авто), минус
    UI/дашборд-эндпоинты. Кэш на процесс; кэшируем только при успешном manifest."""
    global _ontology_cache
    if _ontology_cache is not None:
        return _ontology_cache
    tools: list[ToolSpec] = [
        ToolSpec(
            name="ontology_ask",
            source=ONTOLOGY,
            description="Высокоуровневый вопрос к онтологии на естественном языке: "
            "сама выбирает нужный тул и возвращает утверждения с дословными "
            "цитатами. Для широких/открытых вопросов — когда не ясно, какой "
            "конкретный тул нужен.",
            parameters={"question": {"type": "string"}},
            run=_run_ontology_ask,
        )
    ]
    man = ontology_client.manifest()
    for tname, schema in ((man or {}).get("tools") or {}).items():
        if isinstance(schema, dict) and str(tname) not in _ONTOLOGY_ENDPOINT_ONLY:
            tools.append(_make_ontology_tool(str(tname), schema))
    if man:
        _ontology_cache = tools
        logger.info("ontology tools auto-registered from manifest: %d", len(tools))
    return tools


_KG_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="hybrid_search",
        source=KG,
        description="Поиск релевантных экспериментов в корпусе по текстовому запросу. "
        "Возвращает эксперименты с experiment_id (доказательства) и источниками.",
        parameters={
            "query": {"type": "string", "description": "поисковый запрос на русском"},
            "top_k": {"type": "integer", "description": "сколько вернуть (<=10)"},
        },
        run=_run_hybrid_search,
    ),
    ToolSpec(
        name="graph_rag",
        source=KG,
        description="Ответ по графу знаний (GraphRAG): заземлённый на граф текст с "
        "источниками. Для содержательных вопросов «что известно про …».",
        parameters={"question": {"type": "string"}},
        run=_run_graph_rag,
    ),
]


def catalog_for_mode(mode: ChatMode) -> list[ToolSpec]:
    """Сузить каталог по выбранному источнику знаний. `auto` → синтез обоих."""
    if mode == ChatMode.ONTOLOGY:
        return _ontology_tools()
    if mode == ChatMode.KNOWLEDGE_GRAPH:
        return list(_KG_TOOLS)
    return _ontology_tools() + list(_KG_TOOLS)
