# -*- coding: utf-8 -*-
"""
Mock-агент: эмуляция chat-контура (GraphRAG/LLM-агента) поверх тулов онтологии.

Показывает интеграционный поток «вопрос → интент → вызовы тулов → structured
claims с провенансом» без LLM: интент определяется по ключевым словам, слоты —
по реестрам. Настоящий агент заменяет ровно один слой (интент+синтез текста);
вызовы тулов и форма ответа не меняются.

    python -m ontology.mocks.agent "Какие методы выщелачивания применялись и какое извлечение?"
    python -m ontology.mocks.agent --demo     # прогон контрольных вопросов
"""
from __future__ import annotations

import json
import os
import re
import sys

from . import intent_llm, synth
from .. import query as q
from ..store import Store
from ..tool_service import TOOLS

# LLM-роутер интента (gpt-oss-120b) можно отключить: ONTOLOGY_LLM_INTENT=0
_USE_LLM_INTENT = os.environ.get("ONTOLOGY_LLM_INTENT", "1") != "0"
# LLM-синтез финального ответа из пассажей; отключение: ONTOLOGY_LLM_SYNTH=0
_USE_SYNTH = os.environ.get("ONTOLOGY_LLM_SYNTH", "1") != "0"
# тулы, где ответ синтезируется из текста (ретрив/evidence); структурные — нет
_SYNTH_TOOLS = {"search_passages", "evidence", "evidence_profile",
                "literature_review", "compare_technologies", "compare_practice"}

_NUM = re.compile(r"(\d[\d.,]*)\s*(мг/дм3|мг/дм³|мг/л|%|°C)?")


_CHITCHAT_HELP = (
    "Я — ассистент по базе знаний R&D (горно-металлургия). Могу ответить на "
    "вопросы по корпусу: какое значение свойства получено при процессе, разброс "
    "и оптимум величины по источникам, противоречия между источниками, пробелы в "
    "данных, отечественная vs зарубежная практика, эксперты по теме, цепочка "
    "переделов, статистика базы. Спросите, например: «какое извлечение даёт "
    "хлорирование», «в каком диапазоне коэффициент запаса устойчивости», «кто "
    "занимался аффинажем»."
)


_GREETING_WORDS = (
    "привет", "здравству", "добрый", "доброе", "доброго", "хай", "hi", "hello",
    "hey", "йоу", "дароу", "кто ты", "кто вы", "что ты", "что умеешь",
    "что можешь", "помощь", "help", "спасибо", "пока", "как дела",
)

# Короткие реакции без доменного запроса — не должны попадать в evidence.
_CHITCHAT_REPLIES = (
    "супер", "класс", "круто", "ок", "окей", "okay", "ok", "cool", "nice",
    "great", "thanks", "thx", "ага", "угу", "лол", "хах", "wow", "да", "нет",
)


_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def _is_chitchat(low: str) -> bool:
    """Приветствие / мета / короткая реплика без содержательного запроса.

    Пунктуацию заменяем пробелами перед сопоставлением: знак, прилипший к первому
    слову («привет!»), не должен ломать распознавание приветствия (иначе
    `startswith("привет ")` промахивается и «Привет! Что ты умеешь?» уходит в
    поиск фактов вместо chitchat)."""
    s = re.sub(r"\s+", " ", _PUNCT_RE.sub(" ", low)).strip()
    if len(s) < 3:
        return True
    if s in _CHITCHAT_REPLIES:
        return True
    first = s.split(" ", 1)[0]
    # точное совпадение; «greeting + продолжение»; либо первый токен начинается
    # со стема-приветствия (только длинные стемы ≥5 — «здравству», «привет» —
    # чтобы короткие («пока», «хай») не ловили доменные слова вроде «показатели»).
    return any(
        s == w or s.startswith(w + " ")
        or (" " not in w and len(w) >= 5 and first.startswith(w))
        for w in _GREETING_WORDS
    )


def detect_intent(store: Store, question: str) -> tuple[str, dict]:
    low = question.lower()
    if _is_chitchat(low):
        return "chitchat", {}
    if any(w in low for w in ("противореч", "расхожден", "конфликт")):
        return "find_contradictions", {}
    if any(w in low for w in ("пробел", "не изучен", "нет данных")):
        return "find_gaps", {}
    if any(w in low for w in ("эксперт", "кто занимался", "лаборатор")):
        topic = _topic_slot(low)
        return "find_experts_by_topic", {"topic": topic}
    if any(w in low for w in ("сравни", " vs ", "против", "отечествен", "зарубеж")):
        topic = _topic_slot(low)
        return "compare_practice", {"process": topic}
    if any(w in low for w in ("обзор", "литератур", "что известно")):
        return "literature_review", {"process": _topic_slot(low) or None}
    if any(w in low for w in ("покрыти", "статистик", "сколько в базе")):
        return "coverage", {}
    if any(w in low for w in ("истори", "цепочк", "из чего получ")):
        return "lineage", {"entity": _material_slot(store, low) or _topic_slot(low)}
    # дефолт: числовой вопрос (названа величина) → evidence; вопрос о методах/
    # способах/технических решениях или без величины → ретрив пассажей (всегда
    # с ссылкой на источник), а не no_match → пустой ответ.
    proc = _topic_slot(low)
    mat = _material_slot(store, low)
    qk = _quantity_slot(store, low)
    wants_list = any(w in low for w in (
        "метод", "способ", "техническ", "технолог", "какие", "перечисл",
        "варианты", "виды", "существ", "применяют", "решени"))
    if qk and not wants_list:
        slots: dict = {"quantity_kind": qk}
        if proc:
            slots["process"] = proc
        if mat:
            slots["material"] = mat
        m = re.search(r"(не более|≤|<=|менее)\s*(\d[\d.,]*)", low)
        if m:
            slots["value_op"], slots["value"] = "<=", float(m.group(2).replace(",", "."))
        m = re.search(r"(не менее|≥|>=|более|свыше)\s*(\d[\d.,]*)", low)
        if m:
            slots["value_op"], slots["value"] = ">=", float(m.group(2).replace(",", "."))
        return "evidence", slots
    args: dict = {"query": question}
    if proc:
        args["process"] = proc
    return "search_passages", args


def _from_llm(store: Store, question: str, c: dict) -> tuple[str, dict]:
    """Выход LLM-классификатора → (tool, args) под контракты тулов."""
    intent = c.get("intent")
    proc = (c.get("process") or "").strip() or None
    mat = (c.get("material") or "").strip() or None
    qk = (c.get("quantity_kind") or "").strip() or None
    topic = (c.get("topic") or "").strip() or None
    if intent == "chitchat":
        return "chitchat", {}
    if intent in ("coverage", "find_contradictions", "find_gaps"):
        return intent, {}
    if intent == "find_experts_by_topic":
        return intent, {"topic": topic or proc or question}
    if intent == "compare_practice":
        return intent, {"process": proc or topic or ""}
    if intent == "compare_technologies":
        procs = [p for p in (proc, topic) if p]
        return intent, {"processes": procs or [question]}
    if intent == "lineage":
        return intent, {"entity": mat or topic or proc or question}
    if intent == "timeline":
        args: dict = {}
        if mat:
            args["material"] = mat
        if proc:
            args["process"] = proc
        return intent, args
    if intent == "literature_review":
        return intent, {"process": proc}
    if intent in ("evidence", "evidence_profile") and qk:
        slots: dict = {"quantity_kind": qk}
        if proc:
            slots["process"] = proc
        if mat:
            slots["material"] = mat
        if intent == "evidence":
            op, raw = (c.get("value_op") or ""), (c.get("value") or "")
            num = re.sub(r"[^\d.,]", "", raw).replace(",", ".")
            if op in ("<=", ">=") and num:
                try:
                    slots["value_op"], slots["value"] = op, float(num)
                except ValueError:
                    pass
        return intent, slots
    # search_passages и любой неполный числовой интент → ретрив
    args = {"query": question}
    if proc:
        args["process"] = proc
    return "search_passages", args


def route(
    store: Store, question: str, *, langfuse_headers: dict | None = None
) -> tuple[str, dict]:
    """LLM-роутер (gpt-oss-120b) с откатом на keyword detect_intent."""
    low = question.lower()
    if _is_chitchat(low):
        return "chitchat", {}
    if _USE_LLM_INTENT:
        c = intent_llm.classify(question, langfuse_headers=langfuse_headers)
        if c:
            return _from_llm(store, question, c)
    return detect_intent(store, question)


def _topic_slot(low: str) -> str:
    from ..extract.normalize import _PROCESS_ALIASES
    for alias in sorted(_PROCESS_ALIASES, key=len, reverse=True):
        if len(alias) <= 4:
            continue
        # стем: «выщелачивание» ловит «выщелачиванию/-ем/-я» (падежи)
        if alias in low or alias[:-2] in low:
            return alias
    return ""


def _quantity_slot(store: Store, low: str) -> str:
    """Величина, упомянутая в вопросе — по алиасам реестра quantity_kinds."""
    rows = store.query(
        "SELECT name, unnest(aliases) AS alias FROM experiments.quantity_kinds"
        " WHERE status <> 'needs_review'")
    best_name, best_len = "", 0
    for r in rows:
        a = (r["alias"] or "").lower()
        if len(a) > 4 and (a in low or a[:-2] in low) and len(a) > best_len:
            best_name, best_len = r["name"], len(a)
    return best_name


def _material_slot(store: Store, low: str) -> str:
    """Известный материал, упомянутый в вопросе (по алиасам из БД)."""
    rows = store.query(
        "SELECT alias FROM experiments.entity_aliases"
        " WHERE entity_type='material' AND source='label' AND length(alias) > 4")
    best = ""
    for r in rows:
        a = r["alias"].lower()
        if (a in low or a[:-2] in low) and len(a) > len(best):
            best = r["alias"]
    return best


def answer(
    store: Store, question: str, *, langfuse_headers: dict | None = None,
    synthesize: bool = True,
) -> dict:
    """Форма ответа chat-контура: claims + tools_used + сырой результат тула.
    chitchat — ответ без обращения к БД. Для тулов, если доступна LLM, ответ
    синтезируется естественным языком поверх фактов; structured-claims с
    цитатами идут следом как доказательства."""
    tool, args = route(store, question, langfuse_headers=langfuse_headers)

    if tool == "chitchat":
        return {"question": question, "tools_used": ["chitchat"], "tool_args": {},
                "claims": [{"text": _CHITCHAT_HELP, "kind": "chat",
                            "citations": []}],
                "raw": None}

    if tool == "no_match":
        return {"question": question, "tools_used": [], "tool_args": {},
                "claims": [], "raw": None}

    if tool == "compare_practice" and not args.get("process"):
        tool, args = "find_gaps", {}          # общий гео-вопрос → geo_exclusive пробелов

    # LLM иногда выбирает тул, но не заполняет обязательный слот — дозаполняем
    # из правил по тексту вопроса (иначе тул падает без ключевого аргумента).
    low = question.lower()
    if tool in ("lineage", "get_subgraph") and not args.get("entity"):
        args["entity"] = _material_slot(store, low) or _topic_slot(low)
    if tool == "find_experts_by_topic" and not args.get("topic"):
        args["topic"] = _topic_slot(low) or low
    if tool == "compare_practice" and not args.get("process"):
        args["process"] = _topic_slot(low)

    try:
        result = TOOLS[tool]["fn"](store, **args)
    except Exception:                          # плохой слот — фолбэк на правила
        tool, args = detect_intent(store, question)
        if tool == "chitchat":
            return {"question": question, "tools_used": ["chitchat"], "tool_args": {},
                    "claims": [{"text": _CHITCHAT_HELP, "kind": "chat",
                                "citations": []}], "raw": None}
        result = TOOLS[tool]["fn"](store, **args)

    claims = _claims_from(tool, result)

    # Пустой результат ЛЮБОГО тула (напр. lineage без рёбер под сущность,
    # evidence «данных нет», profile без точек, timeline/experts/compare пусто) —
    # не отдаём «нет данных», а откатываемся на гибридный ретрив пассажей: ответ
    # часто есть в прозе источников, даже если структурный тул его не поднял.
    if not claims and tool != "search_passages":
        tool, args = "search_passages", {"query": question}
        result = TOOLS[tool]["fn"](store, **args)
        claims = _claims_from(tool, result)

    # generation: LLM синтезирует ответ из найденных пассажей (релевантность +
    # числа + честность). Только для ретрив/evidence-тулов; структурные тулы
    # (gaps/coverage/contradictions/experts/lineage/timeline) уже дают точные
    # факты. NO_DATA от синтеза → честная строка, но пассажи оставляем как
    # доказательства (провенанс/числа сохраняются).
    answer_text: str | None = None
    no_answer = False
    if synthesize and _USE_SYNTH and tool in _SYNTH_TOOLS and claims:
        syn = synth.synthesize(question, claims)
        if syn == synth.NO_DATA:
            no_answer = True
        elif syn:
            answer_text = syn

    return {"question": question, "tools_used": [tool], "tool_args": args,
            "claims": claims, "answer": answer_text, "no_answer": no_answer,
            "raw": result}


def _claims_from(tool: str, r) -> list[dict]:
    out = []
    if tool == "evidence" and isinstance(r, dict):
        if r.get("answer") == "данных нет":
            return out
        out.append({"text": r.get("answer", ""), "kind": "fact",
                    "confidence": r.get("confidence"),
                    "n_sources": r.get("n_docs"),
                    "citations": [c.get("snippet", "")[:160]
                                  for c in r.get("citations", [])[:3]]})
    elif tool == "find_contradictions":
        for f in r[:5]:
            if f["type"] == "measurement":
                out.append({"text": f"Расхождение {f['quantity_kind']}: "
                                    f"{f['a']['value']} vs {f['b']['value']} {f['a']['unit']}"
                                    f" ({f['a']['doc']} ↔ {f['b']['doc']})",
                            "kind": "contradiction",
                            "citations": [f["a"]["snippet"][:120], f["b"]["snippet"][:120]]})
            else:
                out.append({"text": f"Выводы расходятся по {f['quantity_kind']}"
                                    f" (фактор: {f['factor']}): {f['directions']}",
                            "kind": "contradiction",
                            "citations": [c["snippet"][:120] for c in f["claims"][:2]]})
    elif tool == "find_gaps":
        for g in r.get("geo_exclusive", [])[:6]:
            side = "только отечественная" if g["only"] == "domestic"                 else "только зарубежная"
            out.append({"text": f"Метод «{g['process_type']}»: {side} практика",
                        "kind": "gap", "citations": []})
        for c in r.get("empty_cells", [])[:5]:
            out.append({"text": f"Нет данных: {c['material']} × {c['quantity_kind']}",
                        "kind": "gap", "citations": []})
        for g in r.get("low_coverage", [])[:3]:
            out.append({"text": f"Мало источников по «{g['process_type']}»:"
                                f" {g['n_sources']}", "kind": "gap", "citations": []})
    elif tool == "find_experts_by_topic":
        for e in r[:5]:
            out.append({"text": f"{e['name']} ({e['city'] or '—'}):"
                                f" {e['n_experiments']} эксп., "
                                + ", ".join(e["expertise"][:3]),
                        "kind": "expert", "citations": []})
    elif tool == "compare_practice":
        out.append({"text": f"Практика по «{r.get('process')}»: отечественных"
                            f" источников {r.get('n_domestic_docs')},"
                            f" зарубежных {r.get('n_foreign_docs')}",
                    "kind": "comparison", "citations": []})
    elif tool == "literature_review":
        methods = [m for m in r.get("by_method", []) if m.get("process_type")
                   and m["process_type"] != "other"]
        for m in methods[:4]:
            out.append({"text": f"Метод «{m['process_type']}»: {m['n_docs']} док.",
                        "kind": "review", "citations": m["docs"][:3]})
        for d in r.get("disagreements", [])[:2]:
            out.append({"text": f"Зона разногласий: {d.get('quantity_kind')}",
                        "kind": "contradiction", "citations": []})
    elif tool == "evidence_profile" and isinstance(r, dict):
        if r.get("n_points"):
            env = r.get("envelope") or {}
            out.append({"text": f"{r['quantity_kind']}: {r['n_points']} сопоставимых "
                                f"точек из {r.get('n_sources', '?')} источников; диапазон "
                                f"{env.get('min')}–{env.get('max')} {env.get('unit') or ''};"
                                f" медиана {r.get('median')}; согласованность:"
                                f" {r.get('agreement')}",
                        "kind": "profile",
                        "citations": [p_.get("snippet", "")[:160]
                                      for p_ in r.get("points", [])[:2]]})
            for p_ in r.get("outliers", [])[:2]:
                out.append({"text": f"Выброс: {p_['value']} {p_.get('unit') or ''} "
                                    f"({p_.get('doc')}, надёжность {p_.get('reliability')})",
                            "kind": "profile", "citations": [p_.get("snippet", "")[:120]]})
    elif tool == "compare_technologies":
        for row in (r or [])[:6]:
            out.append({"text": f"{row['method']} · {row['param']} = {row['value']}"
                                f" {row.get('unit') or ''} ({row.get('origin')})",
                        "kind": "comparison",
                        "citations": [row.get("snippet", "")[:120]]})
    elif tool == "timeline":
        for ev in (r or [])[:6]:
            out.append({"text": f"{ev.get('at') or '—'}: {ev.get('title') or ev.get('process_type') or ''}"
                                f" ({ev.get('lab') or ev.get('doc') or ''})",
                        "kind": "timeline", "citations": []})
    elif tool == "get_subgraph" and isinstance(r, dict):
        preds = {}
        for e in r.get("edges", []):
            preds[e["predicate"]] = preds.get(e["predicate"], 0) + 1
        out.append({"text": f"Окружение узла: {len(r.get('nodes', []))} сущностей, "
                            f"{len(r.get('edges', []))} связей ("
                            + ", ".join(f"{k}×{v}" for k, v in sorted(preds.items())) + ")",
                    "kind": "graph", "citations": []})
    elif tool == "coverage":
        out.append({"text": f"В базе: {json.dumps(r['counts'], ensure_ascii=False)};"
                            f" провенанс 100%: {r['provenance_coverage']}",
                    "kind": "stats", "citations": []})
    elif tool == "lineage":
        for step in r:
            out.append({"text": f"{step['from']} ←[{step['process']}]— {step['to']}",
                        "kind": "lineage", "citations": []})
    elif tool == "search_passages" and isinstance(r, dict):
        for p in r.get("passages", [])[:8]:
            src = p.get("doc") or "источник не указан"
            body = (p.get("text") or p.get("snippet") or "").strip()
            snip = (p.get("snippet") or body)[:140]
            # источник идёт первым в цитате — чтобы имя документа пережило
            # обрезку на стороне чата и ссылка на источник была видна всегда.
            out.append({"text": body, "kind": "fact",
                        "citations": [f"{src}: {snip}"]})
    return out


DEMO_QUESTIONS = [
    "Какое извлечение даёт хлорирование, не менее 90 %?",
    "Какой коэффициент запаса устойчивости встречается в расчётах?",
    "Есть ли противоречия в данных?",
    "Где пробелы в данных?",
    "Какие эксперты занимались хлорированием?",
    "Сравни отечественную и зарубежную практику по выщелачиванию",
    "Из чего получен золотой концентрат?",
    "Что известно про осаждение — литературный обзор",
]


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    store = Store.open()
    questions = (DEMO_QUESTIONS if "--demo" in sys.argv
                 else [" ".join(a for a in sys.argv[1:] if a != "--demo")])
    for qq in questions:
        if not qq.strip():
            continue
        a = answer(store, qq)
        print("=" * 74)
        print("Q:", qq)
        print(f"  → tool: {a['tools_used'][0]} {json.dumps(a['tool_args'], ensure_ascii=False)}")
        for c in a["claims"][:6]:
            print(f"  [{c['kind']}] {c['text'][:110]}")
            for cite in c.get("citations", [])[:1]:
                print(f"        ↳ «{cite[:100]}»")
        if not a["claims"]:
            print("  (пустой результат)")
    store.close()


if __name__ == "__main__":
    main()
