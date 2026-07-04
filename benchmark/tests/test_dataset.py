"""Тесты датасета: поставляемый questions.yaml валиден, gold корректен."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmark.config import DEFAULT_DATASET
from benchmark.dataset import dataset_stats, load_dataset
from benchmark.models import CompetenceQuestion


def test_shipped_dataset_loads_and_is_unique():
    qs = load_dataset(DEFAULT_DATASET)
    assert len(qs) >= 100
    assert len({q.id for q in qs}) == len(qs)  # уникальные id


def test_dataset_covers_flagship_topics():
    qs = load_dataset(DEFAULT_DATASET)
    cats = {q.category for q in qs}
    for must in (
        "water_desalination",
        "electrowinning",
        "pgm_matte_slag",
        "furnace_feed",
        "structural",
        "control",
    ):
        assert must in cats, must
    ids = {q.id for q in qs}
    # экспертный вопрос про глубокие горизонты присутствует как контроль честности
    assert "water_injection-deep-horizons-control" in ids


def test_grounded_questions_have_gold_signal():
    qs = load_dataset(DEFAULT_DATASET)
    answerable = [q for q in qs if q.answerable]
    # у отвечаемых должен быть хоть какой-то сигнал корректности
    for q in answerable:
        g = q.gold
        assert g.must_include_any or g.expected_values, q.id


def test_unanswerable_controls_expect_no_data():
    qs = load_dataset(DEFAULT_DATASET)
    for q in qs:
        if not q.answerable:
            assert q.gold.expect_no_data, q.id


def test_patent_questions_flagged():
    qs = load_dataset(DEFAULT_DATASET)
    pats = [q for q in qs if q.gold.expects_patent]
    assert len(pats) >= 5
    # у патентных вопросов заявлены номера
    assert any(q.gold.patent_numbers for q in pats)


def test_flat_gold_lifting():
    # плоские gold-поля рядом с вопросом поднимаются в gold
    q = CompetenceQuestion.model_validate(
        {
            "id": "x",
            "category": "c",
            "question": "q?",
            "must_include_any": ["штейн"],
            "expects_patent": True,
        }
    )
    assert q.gold.must_include_any == ["штейн"]
    assert q.gold.expects_patent is True


def test_stats_shape():
    qs = load_dataset(DEFAULT_DATASET)
    s = dataset_stats(qs)
    assert s["n"] == len(qs)
    assert s["with_patent"] >= 5
    assert s["n_expected_values"] > 100


def test_bad_dataset_rejected(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "- id: a\n  category: c\n  question: q1?\n"
        "- id: a\n  category: c\n  question: q2?\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_dataset(bad)  # дубликат id
