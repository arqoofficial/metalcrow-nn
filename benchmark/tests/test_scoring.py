"""Тесты свёртки метрик и агрегации."""

from __future__ import annotations

from benchmark import scoring
from benchmark.models import (
    CompetenceQuestion,
    MetricScore,
    QuestionResult,
)


def test_score_question_ignores_inapplicable():
    q = CompetenceQuestion(id="t", category="c", question="q?")
    metrics = [
        MetricScore(name="keywords", score=1.0, weight=1.0),
        MetricScore(name="numeric", score=0.0, weight=0.0, applicable=False),
        MetricScore(name="provenance", score=0.5, weight=1.0),
        MetricScore(name="mode", score=0.0, weight=0.0, applicable=False),
        MetricScore(name="latency", score=1.0, weight=1.0),
    ]
    # применимы keywords(0.30), provenance(0.30), latency(0.10)
    s = scoring.score_question(q, metrics)
    expected = (0.30 * 1.0 + 0.30 * 0.5 + 0.10 * 1.0) / (0.30 + 0.30 + 0.10)
    assert abs(s - expected) < 1e-9


def test_score_question_unanswerable_uses_honesty():
    q = CompetenceQuestion(id="t", category="control", question="q?", answerable=False)
    metrics = [
        MetricScore(name="honesty", score=1.0, weight=1.0),
        MetricScore(name="latency", score=0.2, weight=1.0),
    ]
    assert scoring.score_question(q, metrics) == 1.0


def _r(cat, score, weight=1.0, ok=True, latency=2.0):
    return QuestionResult(
        id=f"{cat}-{score}",
        category=cat,
        question="q?",
        ask_mode="auto",
        ok=ok,
        score=score,
        weight=weight,
        latency_s=latency,
    )


def test_aggregate_weighted_and_by_category():
    results = [
        _r("water", 1.0, weight=1.0),
        _r("water", 0.0, weight=1.0),
        _r("structural", 1.0, weight=0.5),  # низкий вес
        _r("err", 0.0, ok=False),
    ]
    agg = scoring.aggregate(results, pass_threshold=0.6, latency_target_s=5.0)
    assert agg["n_total"] == 4 and agg["n_ok"] == 3 and agg["n_error"] == 1
    # взвешенное среднее по ok: (1*1 + 0*1 + 1*0.5)/(1+1+0.5)=0.6
    assert abs(agg["score"] - 0.6) < 1e-9
    assert agg["by_category"]["water"]["score"] == 0.5
    assert agg["latency"]["within_target"] == 3


def test_aggregate_empty():
    agg = scoring.aggregate([], pass_threshold=0.6, latency_target_s=5.0)
    assert agg["score"] == 0.0 and agg["n_ok"] == 0
