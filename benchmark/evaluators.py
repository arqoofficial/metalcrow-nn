"""Метрики оценки одного ответа чата против gold-эталона.

Каждая метрика — чистая функция, возвращающая `MetricScore` (0..1 + признак
применимости). Комбинирует их в итоговый балл `scoring.py`.

Группы метрик отражают требования ТЗ и озвученные критерии провенанса:
  correctness  — термины методов (keyword) + числовые факты (numeric);
  provenance   — источники, «много цифровых значений», патенты;
  routing      — сработал ли ожидаемый источник/инструмент;
  latency      — НФТ «3–5 c»;
  honesty      — для неотвечаемых вопросов: честное «данных нет».
"""

from __future__ import annotations

import re
from typing import Any

from .models import CompetenceQuestion, ExpectedValue, MetricScore, Op

# ── нормализация текста и извлечение чисел ─────────────────────────────────

_SUP = str.maketrans({"³": "3", "²": "2", " ": " ", " ": " ", " ": " "})
_REF_RE = re.compile(r"\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]")  # библио-ссылки [1], [2,3]
_NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_CITE_RE = re.compile(r"источник\s*[:—-]", re.I)
_PATENT_RE = re.compile(
    r"(пат\.?\s|патент\w*|а\.?\s?с\.?\s|авторск\w+\s+свидетельств\w*"
    r"|\bRU\s?\d{6,7}|\bSU\s?\d{6,7})",
    re.I,
)
_PATENT_NUM_RE = re.compile(r"(?:RU|SU)?\s?\d{6,7}\s?[СCАA]\d?", re.I)


def normalize(text: str) -> str:
    return (text or "").translate(_SUP)


def extract_numbers(text: str) -> list[float]:
    """Числовые токены без библио-ссылок. Comma→dot."""
    clean = _REF_RE.sub(" ", normalize(text))
    out: list[float] = []
    for tok in _NUM_RE.findall(clean):
        try:
            out.append(float(tok.replace(",", ".")))
        except ValueError:
            continue
    return out


def count_numeric(text: str) -> int:
    return len(extract_numbers(text))


def count_citations(text: str) -> int:
    return len(_CITE_RE.findall(text or ""))


def has_patent(text: str) -> bool:
    return bool(_PATENT_RE.search(text or ""))


def patent_numbers(text: str) -> list[str]:
    return [m.group(0).strip() for m in _PATENT_NUM_RE.finditer(normalize(text))]


# ── разбор ответа чата ────────────────────────────────────────────────────


def flatten_answer(payload: dict[str, Any]) -> dict[str, Any]:
    """Собрать текст ответа (summary + тексты claim'ов) и провенанс-счётчики
    из ChatMessageResponse."""
    summary = payload.get("summary", "") or ""
    claims = payload.get("claims", []) or []
    parts = [summary]
    n_experiment_ids = 0
    for c in claims:
        if not isinstance(c, dict):
            continue
        parts.append(c.get("text", "") or "")
        n_experiment_ids += len(c.get("experiment_ids") or [])
    text = "\n".join(p for p in parts if p)
    return {
        "text": text,
        "mode_used": payload.get("mode_used"),
        "tools_used": payload.get("tools_used", []) or [],
        "n_numeric": count_numeric(text),
        "n_citations": count_citations(text),
        "n_experiment_ids": n_experiment_ids,
        "has_patent": has_patent(text),
    }


# ── сопоставление ключевых слов (с терпимостью к рус. словоформам) ─────────


def contains(text_low: str, term: str) -> bool:
    t = term.strip().lower()
    if not t:
        return False
    if t in text_low:
        return True
    # лёгкий стемминг: «обессоливание» ловит «обессоливания/-ю/-ем»
    if len(t) > 5 and t[:-2] in text_low:
        return True
    if len(t) > 7 and t[:-3] in text_low:
        return True
    return False


def eval_keywords(q: CompetenceQuestion, text: str) -> MetricScore:
    g = q.gold
    low = text.lower()
    parts: list[float] = []
    detail: list[str] = []
    if g.must_include_any:
        hit = [t for t in g.must_include_any if contains(low, t)]
        parts.append(1.0 if hit else 0.0)
        detail.append(f"any {len(hit)}/{len(g.must_include_any)}")
    if g.must_include_all:
        hit = [t for t in g.must_include_all if contains(low, t)]
        parts.append(len(hit) / len(g.must_include_all))
        detail.append(f"all {len(hit)}/{len(g.must_include_all)}")
    if not parts:
        return MetricScore(
            name="keywords",
            score=0.0,
            weight=0.0,
            applicable=False,
            detail="нет must_include",
        )
    return MetricScore(
        name="keywords",
        score=sum(parts) / len(parts),
        weight=1.0,
        detail=", ".join(detail),
    )


# ── числовые факты ────────────────────────────────────────────────────────


def _value_present(nums: list[float], ev: ExpectedValue) -> bool:
    tol = ev.tol if ev.tol > 0 else max(0.5, abs(ev.value) * 0.02)
    if ev.op == Op.RANGE and ev.value2 is not None:
        lo, hi = sorted((ev.value, ev.value2))
        span = (hi - lo) * 0.02
        if any(lo - span <= n <= hi + span for n in nums):
            return True
        return any(abs(n - lo) <= tol for n in nums) and any(
            abs(n - hi) <= max(0.5, abs(hi) * 0.02) for n in nums
        )
    # =, ~, <=, >= : достаточно, чтобы граница/значение фигурировали в ответе
    return any(abs(n - ev.value) <= tol for n in nums)


def eval_numeric(q: CompetenceQuestion, text: str) -> MetricScore:
    evs = q.gold.expected_values
    if not evs:
        return MetricScore(
            name="numeric",
            score=0.0,
            weight=0.0,
            applicable=False,
            detail="нет expected_values",
        )
    nums = extract_numbers(text)
    hit = [ev for ev in evs if _value_present(nums, ev)]
    return MetricScore(
        name="numeric",
        score=len(hit) / len(evs),
        weight=1.0,
        detail=f"{len(hit)}/{len(evs)} значений",
    )


# ── провенанс: источники + цифровая насыщенность + патенты ────────────────


def eval_provenance(q: CompetenceQuestion, flat: dict[str, Any]) -> MetricScore:
    g = q.gold
    # Вопрос без провенанс-ожиданий (структурный/маршрутный) — метрику не считаем.
    if (
        not g.expected_sources
        and not g.expects_patent
        and not g.min_citations
        and not g.min_numeric_values
    ):
        return MetricScore(
            name="provenance",
            score=0.0,
            weight=0.0,
            applicable=False,
            detail="нет провенанс-ожиданий",
        )
    text = flat["text"]
    low = text.lower()
    comps: list[float] = []
    detail: list[str] = []

    # 1) источники: цитаты в тексте / experiment_ids / совпадение названий док-тов
    src_hit = (
        any(contains(low, s) for s in g.expected_sources)
        if g.expected_sources
        else False
    )
    cited = flat["n_citations"] > 0 or flat["n_experiment_ids"] > 0 or src_hit
    need_cites = max(1, g.min_citations)
    have_cites = flat["n_citations"] + (1 if src_hit else 0)
    comps.append(min(1.0, have_cites / need_cites) if cited else 0.0)
    detail.append(
        f"цит {flat['n_citations']}+exp {flat['n_experiment_ids']}"
        f"{' +src' if src_hit else ''}"
    )

    # 2) «много цифровых значений»
    floor = g.min_numeric_values or 3
    comps.append(min(1.0, flat["n_numeric"] / floor))
    detail.append(f"чисел {flat['n_numeric']}/{floor}")

    # 3) патенты (метрика применяется, только если вопрос их ожидает)
    if g.expects_patent:
        comps.append(1.0 if flat["has_patent"] else 0.0)
        detail.append("патент " + ("да" if flat["has_patent"] else "нет"))

    return MetricScore(
        name="provenance",
        score=sum(comps) / len(comps),
        weight=1.0,
        detail=", ".join(detail),
    )


# ── маршрутизация режима/инструмента (мягкая) ─────────────────────────────


def eval_mode(q: CompetenceQuestion, flat: dict[str, Any]) -> MetricScore:
    mode_used = (flat.get("mode_used") or "").lower()
    tools = [str(t).lower() for t in flat.get("tools_used", [])]
    exp_mode = q.expected_mode.value
    parts: list[float] = []
    detail: list[str] = []
    if exp_mode != "auto":
        parts.append(1.0 if mode_used == exp_mode else 0.0)
        detail.append(f"mode {mode_used or '—'}≟{exp_mode}")
    if q.expected_tools_any:
        want = [t.lower() for t in q.expected_tools_any]
        hit = any(any(w in tool for tool in tools) for w in want)
        parts.append(1.0 if hit else 0.0)
        detail.append("tool " + ("да" if hit else "нет"))
    if not parts:
        return MetricScore(name="mode", score=0.0, weight=0.0, applicable=False)
    return MetricScore(
        name="mode", score=sum(parts) / len(parts), weight=1.0, detail=", ".join(detail)
    )


# ── латентность (НФТ 3–5 c) ───────────────────────────────────────────────


def eval_latency(latency_s: float, target_s: float) -> MetricScore:
    if latency_s <= target_s:
        score = 1.0
    elif latency_s <= 2 * target_s:
        score = 0.5
    else:
        score = max(0.0, 1.0 - (latency_s - target_s) / (3 * target_s))
    return MetricScore(
        name="latency",
        score=score,
        weight=1.0,
        detail=f"{latency_s:.1f}c / цель {target_s:.0f}c",
    )


# ── честность для неотвечаемых контролей ──────────────────────────────────

_NODATA_RE = re.compile(
    r"нет данных|не найдено|ничего не найдено|не содержит|отсутству|не удалось найти"
    r"|нет информации|данных нет|не нашл|в корпусе нет|нет в корпусе|не располага"
    r"|no data|not found|no relevant|does ?n.?t contain|not contain|no information",
    re.I,
)


def eval_honesty(q: CompetenceQuestion, flat: dict[str, Any]) -> MetricScore:
    text = flat["text"]
    said_nodata = bool(_NODATA_RE.search(text))
    forbidden = [t for t in q.gold.forbid_include if contains(text.lower(), t)]
    # честный ответ: признал отсутствие данных и не «выдумал» запретных фактов
    score = 1.0 if said_nodata else 0.0
    if forbidden:
        score = min(score, 0.0)
    return MetricScore(
        name="honesty",
        score=score,
        weight=1.0,
        detail=("нет-данных" if said_nodata else "выдал ответ")
        + (f", запретных {len(forbidden)}" if forbidden else ""),
    )
