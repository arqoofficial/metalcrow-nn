"""Загрузка и валидация датасета competence-вопросов (`data/questions.yaml`).

YAML верхнего уровня — либо список вопросов, либо `{questions: [...]}`.
Каждый элемент валидируется в `CompetenceQuestion` (плоские gold-поля
поднимаются в `gold` автоматически). Валидатор ловит дубли id и пустые вопросы
ещё до обращения к сервису — поэтому запускается и в оффлайне, и в тестах.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from .models import CompetenceQuestion


def _raw_items(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "questions" in data:
        data = data["questions"]
    if not isinstance(data, list):
        raise ValueError(f"{path}: ожидался список вопросов или {{questions: [...]}}")
    return data


def load_dataset(path: str | Path) -> list[CompetenceQuestion]:
    path = Path(path)
    items = _raw_items(path)
    out: list[CompetenceQuestion] = []
    errors: list[str] = []
    for i, raw in enumerate(items):
        try:
            out.append(CompetenceQuestion.model_validate(raw))
        except Exception as exc:  # pydantic ValidationError и пр.
            errors.append(f"  #{i} ({raw.get('id', '?')}): {exc}")
    if errors:
        raise ValueError(f"{path}: невалидные вопросы:\n" + "\n".join(errors))
    _check_unique_ids(out)
    return out


def _check_unique_ids(qs: list[CompetenceQuestion]) -> None:
    dups = [i for i, c in Counter(q.id for q in qs).items() if c > 1]
    if dups:
        raise ValueError(f"дублирующиеся id: {dups}")


def dataset_stats(qs: list[CompetenceQuestion]) -> dict[str, Any]:
    return {
        "n": len(qs),
        "by_category": dict(sorted(Counter(q.category for q in qs).items())),
        "by_lang": dict(Counter(q.lang.value for q in qs)),
        "by_difficulty": dict(Counter(q.difficulty.value for q in qs)),
        "answerable": sum(q.answerable for q in qs),
        "unanswerable": sum(not q.answerable for q in qs),
        "with_values": sum(1 for q in qs if q.gold.expected_values),
        "with_patent": sum(1 for q in qs if q.gold.expects_patent),
        "n_expected_values": sum(len(q.gold.expected_values) for q in qs),
    }
