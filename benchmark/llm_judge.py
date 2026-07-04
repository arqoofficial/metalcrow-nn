"""Опциональный LLM-судья (OpenAI-совместимый chat/completions).

Оценивает ответ чата по трём осям (0..1): фактическая корректность,
полнота, заземлённость на источники — сверяясь с `grounding_note` и gold.
Включается флагом `--judge` при заданных base_url+api_key. Любая ошибка →
`None` (метрика просто не учитывается, прогон не падает).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .config import BenchConfig
from .models import CompetenceQuestion

logger = logging.getLogger("benchmark.judge")

_SYSTEM = (
    "Ты — строгий оценщик RAG-ответов по горно-металлургическому корпусу. "
    "Тебе дают вопрос, эталонную заметку о правильном ответе (grounding) и ответ "
    "системы. Оцени ответ системы по трём осям от 0 до 1: correctness (факты и "
    "числа совпадают с эталоном), completeness (насколько полно покрыты нужные "
    "методы/значения), grounding (есть ли ссылки на источники и конкретные числа). "
    'Отвечай СТРОГО JSON: {"correctness":x,"completeness":x,"grounding":x,'
    '"verdict":"pass|weak|fail","rationale":"..."}. Не выдумывай факты.'
)


def _prompt(q: CompetenceQuestion, answer: str) -> str:
    gold = {
        "must_include_any": q.gold.must_include_any,
        "expected_values": [ev.model_dump() for ev in q.gold.expected_values],
        "expected_sources": q.gold.expected_sources,
        "expects_patent": q.gold.expects_patent,
        "patent_numbers": q.gold.patent_numbers,
    }
    return (
        f"ВОПРОС:\n{q.question}\n\n"
        f"ЭТАЛОН (grounding_note):\n{q.grounding_note}\n\n"
        f"GOLD (структурно):\n{json.dumps(gold, ensure_ascii=False)}\n\n"
        f"ОТВЕТ СИСТЕМЫ:\n{answer or '(пусто)'}\n\n"
        "Верни только JSON."
    )


def judge_answer(
    cfg: BenchConfig, q: CompetenceQuestion, answer: str
) -> dict[str, Any] | None:
    if not (cfg.judge_base_url and cfg.judge_api_key):
        return None
    url = cfg.judge_base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": cfg.judge_model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _prompt(q, answer)},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    try:
        with httpx.Client(timeout=60.0, trust_env=False) as h:
            r = h.post(
                url, json=body, headers={"Authorization": f"Bearer {cfg.judge_api_key}"}
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        data["score"] = round(
            (
                float(data.get("correctness", 0))
                + float(data.get("completeness", 0))
                + float(data.get("grounding", 0))
            )
            / 3,
            3,
        )
        return data
    except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("judge failed for %s: %s", q.id, exc)
        return None
