"""ReAct-цикл над каталогом тулов ретрива (SPEC_V3 §5.7).

Планировщик на строгом JSON (`llm.complete_json`) работает на всех моделях
gateway независимо от поддержки нативного tool-calling. Схема шага:
  1. plan  — LLM выбирает ОДИН тул (или done), видя вопрос и уже собранные
     наблюдения;
  2. act   — тул исполняется, наблюдение и его провенанс кладутся в пул;
  3. повтор до `LLM_AGENT_MAX_STEPS`/дедлайна или явного done;
  4. synth — LLM собирает claims (каждый со ссылками на пул источников) + summary;
  5. ground — ссылки резолвятся в реальные experiment_id / строки-источники,
     claim без источника понижается до confidence=low (анти-галлюцинации).

Любая невосстановимая ситуация (LLM недоступен, ноль собранного провенанса, ноль
валидных claims) поднимается как `LLMUnavailable` — вызывающий
(`chat.answer_message`) откатывается на детерминированный водопад. Персистентность
сообщений остаётся на стороне `chat.answer_message`; здесь только чтение.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlmodel import Session

from app.core.config import settings
from app.schemas.chat import (
    ChatMessageRequest,
    ChatMessageResponse,
    ChatMode,
    ChatSource,
    Claim,
    ClaimConfidence,
    ClaimKind,
)
from app.services.agent import tools as tool_registry
from app.services.agent.llm import LLMUnavailable, complete_json

logger = logging.getLogger(__name__)

_PLANNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "tools": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "args": {"type": "object"},
                },
            },
        },
        "done": {"type": "boolean"},
    },
    "required": ["thought", "tools", "done"],
    "additionalProperties": False,
}

_SYNTH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "kind": {"type": "string", "enum": ["fact", "hypothesis"]},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "confidence", "kind", "evidence_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "claims"],
    "additionalProperties": False,
}


def _format_history(history: list[tuple[str, str]] | None) -> str:
    """Компактный блок последних реплик диалога для планировщика и синтеза —
    чтобы уточняющие вопросы («а что это значит?») резолвились по контексту."""
    if not history:
        return ""
    lines = []
    for role, content in history[-6:]:
        who = "Пользователь" if role == "user" else "Ассистент"
        lines.append(f"{who}: {_clip(content, 400)}")
    return "Контекст диалога (последние реплики):\n" + "\n".join(lines) + "\n\n"


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _parse_calls(
    step: dict[str, Any], catalog: dict[str, Any], default_query: str
) -> list[tuple[str, dict[str, Any]]]:
    """Толерантно достать список (tool, args) из шага планировщика: модель
    непостоянна — tools[] или одиночный tool/name/action, args/tool_input/arguments.
    Отсутствующий основной аргумент запроса подставляем из вопроса пользователя."""
    items = step.get("tools")
    if not isinstance(items, list):
        single = step.get("tool") or step.get("name") or step.get("action")
        items = [{"tool": single, "args": step.get("args") or step.get("tool_input")}]
    calls: list[tuple[str, dict[str, Any]]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("tool") or item.get("name") or item.get("action")
        if not name or name not in catalog:
            continue
        raw = (
            item.get("args")
            or item.get("tool_input")
            or item.get("arguments")
            or item.get("parameters")
            or {}
        )
        args = dict(raw) if isinstance(raw, dict) else {}
        # Дефолт-запрос подставляем ТОЛЬКО в объявленные тулом параметры query/
        # question, и фильтруем args на схему тула — иначе онто /invoke падает на
        # «unexpected keyword argument» (строгие сигнатуры).
        props = catalog[str(name)].parameters or {}
        for key in ("query", "question"):
            if key in props and not args.get(key):
                args[key] = default_query
        if props:
            args = {k: v for k, v in args.items() if k in props}
        calls.append((str(name), args))
    return calls


def run_agent(
    session: Session,
    chat_session_id: uuid.UUID,
    request: ChatMessageRequest,
    history: list[tuple[str, str]] | None = None,
) -> ChatMessageResponse:
    mode = request.metadata.mode if request.metadata else ChatMode.AUTO
    history_block = _format_history(history)
    catalog = {t.name: t for t in tool_registry.catalog_for_mode(mode)}
    if not catalog:
        raise LLMUnavailable("empty tool catalog for mode")

    deadline = time.monotonic() + settings.LLM_AGENT_DEADLINE_S
    observations: list[str] = []
    pool_exp: dict[str, str] = {}  # "E1" -> experiment_id
    pool_src: dict[str, str] = {}  # "S1" -> строка-источник
    chat_pool: list[ChatSource] = []  # структурные источники (run-level, для чипов)
    chat_keys: set[str] = set()
    sources_used: set[str] = set()
    seen_calls: set[str] = set()
    productive = False  # хоть один тул вернул реальные данные (obs.ok)

    def absorb(name: str, obs: tool_registry.Observation) -> None:
        """Поглотить наблюдение тула в пулы провенанса (общий код цикла и
        ретрив-предохранителя)."""
        nonlocal productive
        if obs.ok:
            productive = True
            sources_used.add(catalog[name].source)
        for eid in obs.experiment_ids:
            if eid not in pool_exp.values():
                pool_exp[f"E{len(pool_exp) + 1}"] = eid
        for src in obs.sources:
            if src not in pool_src.values():
                pool_src[f"S{len(pool_src) + 1}"] = src
        for cs in obs.chat_sources:
            key = cs.source_path or cs.filename or cs.doc_id
            if key and key not in chat_keys:
                chat_keys.add(key)
                chat_pool.append(cs)
        observations.append(f"[{name}] {obs.text}")

    tools_catalog_text = "\n".join(
        f"- {t.name}: {t.description} args={json.dumps(t.parameters, ensure_ascii=False)}"
        for t in catalog.values()
    )
    planner_system = (
        "Ты — ассистент-исследователь по металлургии и электрохимии. У тебя есть "
        "тулы ретрива по базе экспериментов и графу знаний. На каждом раунде выбери "
        "ОДИН ИЛИ НЕСКОЛЬКО тулов для параллельного вызова (они исполняются "
        "одновременно), либо заверши сбор (done=true), когда данных достаточно. "
        "Онто-тулы быстрые — не бойся вызвать 2-3 сразу разными формулировками. "
        "Если узкий тул (lineage/timeline/evidence/…) вернул «данных нет», НЕ "
        "завершай сразу: сначала попробуй широкий ретрив — ontology_ask с полным "
        "вопросом пользователя (или hybrid_search). Заключай «нет данных» только "
        "если и широкий ретрив пуст. "
        "НИКОГДА не выдумывай факты — только из тулов.\n\n"
        'Ответ СТРОГО в JSON: {"thought": "...", "tools": [{"tool": "<имя из списка>", '
        '"args": {<аргументы>}}, ...], "done": false}. Имена — только из списка.\n\n'
        f"Доступные тулы:\n{tools_catalog_text}"
    )

    for _ in range(settings.LLM_AGENT_MAX_STEPS):
        if time.monotonic() > deadline:
            break
        planner_user = (
            history_block
            + f"Вопрос пользователя: {request.content}\n\n"
            "Собранные наблюдения:\n"
            + ("\n\n".join(observations) if observations else "(пока пусто)")
            + "\n\nВыбери тулы (можно несколько) или заверши (done=true)."
        )
        step = complete_json(
            planner_system,
            planner_user,
            schema=_PLANNER_SCHEMA,
            schema_name="plan",
            strict=False,  # args — свободная форма, strict-режим отверг бы схему
        )
        if step.get("done"):
            break
        calls = [
            c
            for c in _parse_calls(step, catalog, request.content)
            if f"{c[0]}:{json.dumps(c[1], ensure_ascii=False, sort_keys=True)}"
            not in seen_calls
        ][: settings.LLM_AGENT_MAX_PARALLEL_TOOLS]
        if not calls:
            break
        for name, args in calls:
            seen_calls.add(f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}")

        # Параллельный диспатч: тулы — I/O (HTTP к сайдкарам), пул потоков быстр.
        # Сессию БД трогает только hybrid_search, и дедуп не даёт двум таким в батче.
        def _dispatch(call: tuple[str, dict[str, Any]]) -> tuple[str, Any]:
            name, args = call
            try:
                return name, catalog[name].run(session, args)
            except Exception as exc:  # тул не должен валить весь батч
                return name, exc

        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            results = list(pool.map(_dispatch, calls))

        for name, obs in results:
            if isinstance(obs, BaseException):
                logger.warning("agent tool %s failed: %s", name, obs)
                continue
            logger.info(
                "agent step tool=%s ok=%s sources=%d exp=%d",
                name, obs.ok, len(obs.sources), len(obs.experiment_ids),
            )
            absorb(name, obs)

    # Планировщик мог выбрать только узкие тулы (напр. lineage без рёбер под
    # сущность) и не попробовать широкий ретрив. Прежде чем честно сказать «нет
    # данных» — ГАРАНТИРОВАННАЯ попытка широкого ретрива по полному вопросу:
    # ontology_ask (умный роутер онтологии с фолбэком на search_passages), а в
    # KG-режиме — hybrid_search. Не полагаемся на дисциплину планировщика.
    if not productive and not pool_exp and not pool_src:
        tried = seen_calls_to_names(seen_calls)
        fb = next((n for n in ("ontology_ask", "hybrid_search")
                   if n in catalog and n not in tried), None)
        if fb:
            try:
                fb_obs = catalog[fb].run(
                    session, {"question": request.content, "query": request.content})
            except Exception as exc:
                logger.warning("agent fallback %s failed: %s", fb, exc)
                fb_obs = None
            if fb_obs is not None:
                logger.info("agent fallback tool=%s ok=%s", fb, fb_obs.ok)
                seen_calls.add(f"{fb}:{{}}")
                absorb(fb, fb_obs)

    # Ни один тул (включая ретрив-предохранитель) не дал данных → честный «нет
    # данных» ОТ АГЕНТА, а не откат на водопад: тот на внекорпусных вопросах
    # вываливает нерелевантные пассажи. (Откат на водопад — только на реальный
    # сбой LLM, LLMUnavailable.)
    if not productive and not pool_exp and not pool_src:
        return _no_data_response(chat_session_id, mode)

    evidence_lines = [f"[{ref}] эксперимент {eid}" for ref, eid in pool_exp.items()]
    evidence_lines += [f"[{ref}] {src}" for ref, src in pool_src.items()]
    synth_system = (
        "Ты пишешь ответ на РУССКОМ языке СТРОГО по наблюдениям ретрива ниже.\n"
        "СИНТЕЗИРУЙ, а не копируй: 1–4 КОРОТКИХ самодостаточных утверждения (claim), "
        "каждое своими словами, с материалом/процессом/величиной где уместно. НЕ "
        "вставляй длинные дословные пассажи как claim.\n"
        "Включай конкретные числовые значения с единицами, если они есть в "
        "наблюдениях. Числа бери дословно из наблюдений, не выдумывай.\n"
        "Каждый claim ОБЯЗАН ссылаться на подтверждающие его источники через "
        "evidence_ids (метки S1/E1).\n"
        "ЕСЛИ источники не отвечают на вопрос по существу — верни РОВНО ОДИН claim "
        "«В корпусе нет данных по этому вопросу» с confidence=low и evidence_ids=[], "
        "без посторонних фактов и нерелевантных пассажей.\n"
        "Если вопрос — уточнение к предыдущему ответу, учитывай контекст диалога.\n"
        "summary — 1–2 связных предложения, а не копия пассажа."
    )
    synth_user = (
        history_block
        + f"Вопрос: {request.content}\n\n"
        "Наблюдения:\n" + "\n\n".join(observations) + "\n\n"
        "Источники (ссылайся на них в evidence_ids):\n"
        + ("\n".join(evidence_lines) or "(нет источников)")
    )
    synth = complete_json(
        synth_system, synth_user, schema=_SYNTH_SCHEMA, schema_name="answer"
    )

    claims = _ground(synth.get("claims") or [], pool_exp, pool_src, chat_pool)
    if not claims:
        raise LLMUnavailable("agent produced no valid claims")

    summary = str(synth.get("summary") or claims[0].text.split("\n")[0])
    return ChatMessageResponse(
        claims=claims,
        summary=summary,
        tools_used=sorted(seen_calls_to_names(seen_calls)),
        subgraph=None,
        session_id=chat_session_id,
        mode_used=_mode_used(sources_used),
    )


def seen_calls_to_names(seen_calls: set[str]) -> set[str]:
    return {call.split(":", 1)[0] for call in seen_calls}


def _no_data_response(
    chat_session_id: uuid.UUID, mode: ChatMode
) -> ChatMessageResponse:
    """Честный ответ агента, когда ретрив не дал релевантных данных."""
    mode_used = {
        ChatMode.ONTOLOGY: "ontology",
        ChatMode.KNOWLEDGE_GRAPH: "knowledge_graph",
    }.get(mode, "knowledge_graph")
    claim = Claim(
        text="В корпусе нет данных по этому вопросу.",
        experiment_ids=[],
        confidence=ClaimConfidence.LOW,
        kind=ClaimKind.FACT,
    )
    return ChatMessageResponse(
        claims=[claim],
        summary=claim.text,
        tools_used=[],
        subgraph=None,
        session_id=chat_session_id,
        mode_used=mode_used,
    )


def _mode_used(sources_used: set[str]) -> str:
    if tool_registry.ONTOLOGY in sources_used and tool_registry.KG in sources_used:
        return "both"
    if tool_registry.ONTOLOGY in sources_used:
        return "ontology"
    return "knowledge_graph"


_MAX_CLAIMS = 6
_MAX_CLAIM_LEN = 500


def _ground(
    claims_raw: list[Any],
    pool_exp: dict[str, str],
    pool_src: dict[str, str],
    chat_pool: list[ChatSource] | None = None,
) -> list[Claim]:
    """Провенанс проставляет ТОЛЬКО по ссылкам, которые дал синтез (evidence_ids);
    без свалки всего пула. Claim без валидной ссылки → confidence=low и без строки
    источника (честно, а не выдуманная атрибуция). Текст усечён — не даём вставлять
    простыни-пассажи. Заземлённым claim'ам прикрепляются структурные ChatSource-чипы
    (run-level, как experiment_ids) — кликабельные источники наравне с водопадом."""
    out: list[Claim] = []
    for raw in claims_raw[:_MAX_CLAIMS]:
        if not isinstance(raw, dict):
            continue
        text = _clip(str(raw.get("text") or "").strip(), _MAX_CLAIM_LEN)
        if not text:
            continue
        refs = [str(x) for x in (raw.get("evidence_ids") or [])]
        exp = [pool_exp[r] for r in refs if r in pool_exp]
        src = [pool_src[r] for r in refs if r in pool_src]
        confidence = _conf(raw.get("confidence"))
        kind = _kind(raw.get("kind"))
        if not exp and not src:
            confidence = ClaimConfidence.LOW  # нет источника → только low, без цитаты
        body = f"{text}\n— источник: «{src[0]}»" if src else text
        # чипы только заземлённым claim'ам (есть цитата/эксперимент), не «нет данных»
        sources = (chat_pool or [])[:5] if (src or exp) else []
        out.append(
            Claim(
                text=body,
                experiment_ids=_to_uuids(exp),
                confidence=confidence,
                kind=kind,
                sources=sources,
            )
        )
    return out


def _to_uuids(values: list[str]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for v in values:
        try:
            out.append(uuid.UUID(v))
        except (ValueError, AttributeError):
            continue
    return out


def _conf(value: Any) -> ClaimConfidence:
    try:
        return ClaimConfidence(str(value))
    except ValueError:
        return ClaimConfidence.LOW


def _kind(value: Any) -> ClaimKind:
    try:
        return ClaimKind(str(value))
    except ValueError:
        return ClaimKind.FACT
