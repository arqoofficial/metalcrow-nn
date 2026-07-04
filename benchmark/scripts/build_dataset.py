"""Собрать итоговый датасет `data/questions.yaml` из двух источников:

  1. `data/generated/grounded_raw.json` — grounded-вопросы, сгенерированные и
     выверенные по документам корпуса (см. README, раздел «Генерация»).
  2. `data/seed_questions.yaml` — ручные флагманы ТЗ/экспертов, структурные,
     контроли честности, мультиязычные.

Нормализует grounded-вопросы: тема берётся из префикса id (интент-подпись
модели переносится в `expected_tools_any`), плоские gold-поля кладутся под
`gold`. Всё проходит валидацию через `CompetenceQuestion` перед записью.

Запуск:  python -m benchmark.scripts.build_dataset
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[2]  # metalcrow/
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmark.dataset import dataset_stats  # noqa: E402
from benchmark.models import CompetenceQuestion  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
RAW = DATA / "generated" / "grounded_raw.json"
SEED = DATA / "seed_questions.yaml"
OUT = DATA / "questions.yaml"

# префикс id (тема) → категория итогового датасета
CATEGORY_REMAP = {
    "water_injection": "water_treatment",  # док-ты = очистка шахтных вод
    "water_treatment_desal": "water_desalination",
}

VALID_TOOLS = {
    "evidence",
    "evidence_profile",
    "find_gaps",
    "find_contradictions",
    "compare_practice",
    "compare_technologies",
    "find_experts_by_topic",
    "get_subgraph",
    "lineage",
    "timeline",
    "literature_review",
    "coverage",
}

GOLD_KEYS = (
    "must_include_any",
    "must_include_all",
    "forbid_include",
    "expected_values",
    "expected_sources",
    "expects_patent",
    "patent_numbers",
    "min_citations",
    "min_numeric_values",
    "expect_no_data",
)


def _topic(qid: str) -> str:
    return qid.split("-", 1)[0]


def normalize_generated(raw: list[dict]) -> list[dict]:
    out = []
    for item in raw:
        topic = _topic(item["id"])
        intent = item.get("category", "")
        tools = [intent] if intent in VALID_TOOLS else []
        gold = {k: item[k] for k in GOLD_KEYS if k in item}
        q = {
            "id": item["id"],
            "lang": item.get("lang", "ru"),
            "category": CATEGORY_REMAP.get(topic, topic),
            "question": item["question"],
            "expected_mode": item.get("expected_mode", "auto"),
            "expected_tools_any": tools,
            "answerable": item.get("answerable", True),
            "difficulty": item.get("difficulty", "medium"),
            "grounding_note": item.get("grounding_note", ""),
            "weight": 1.0,
            "gold": gold,
        }
        out.append(q)
    return out


def load_seed() -> list[dict]:
    data = yaml.safe_load(SEED.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("questions", [])


def _dump_question(q: dict) -> dict:
    """Упорядочить ключи для читаемого YAML."""
    order = [
        "id",
        "lang",
        "category",
        "question",
        "expected_mode",
        "expected_tools_any",
        "answerable",
        "difficulty",
        "weight",
        "grounding_note",
        "gold",
    ]
    return {k: q[k] for k in order if k in q}


def main() -> int:
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    generated = normalize_generated(raw)
    seed = load_seed()

    merged = seed + sorted(generated, key=lambda q: (q["category"], q["id"]))

    # валидация + проверка уникальности id через loader-модель
    validated: list[CompetenceQuestion] = []
    seen: set[str] = set()
    for q in merged:
        cq = CompetenceQuestion.model_validate(q)
        if cq.id in seen:
            raise SystemExit(f"дубликат id: {cq.id}")
        seen.add(cq.id)
        validated.append(cq)

    header = (
        "# Датасет competence-вопросов для бенчмарка чата (см. benchmark/README.md).\n"
        "# СОБРАН автоматически: benchmark/scripts/build_dataset.py\n"
        "#   источники: data/seed_questions.yaml + data/generated/grounded_raw.json\n"
        "# Правьте seed_questions.yaml / grounded_raw.json и пересоберите, либо\n"
        "# редактируйте этот файл напрямую — формат один (список CompetenceQuestion).\n"
        f"# Всего вопросов: {len(merged)}\n\n"
    )
    body = yaml.safe_dump(
        [_dump_question(q) for q in merged],
        allow_unicode=True,
        sort_keys=False,
        width=100,
        default_flow_style=False,
    )
    OUT.write_text(header + body, encoding="utf-8")

    stats = dataset_stats(validated)
    print(
        f"Записан {OUT} — {len(merged)} вопросов "
        f"(seed {len(seed)} + generated {len(generated)})"
    )
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
