"""Свёртка метрик в итоговый балл вопроса и агрегатов прогона.

Веса групп (по умолчанию) отражают акцент задачи на корректность и провенанс:
    correctness (keywords+numeric) 0.50 · provenance 0.30 · mode 0.10 · latency 0.10
Неприменимые метрики (нет expected_values и т.п.) выпадают, веса
перенормируются по оставшимся. Для неотвечаемых вопросов балл = honesty.
"""

from __future__ import annotations

import statistics
from typing import Any

from .models import CompetenceQuestion, MetricScore, QuestionResult

# базовые веса по имени метрики
WEIGHTS: dict[str, float] = {
    "keywords": 0.30,
    "numeric": 0.20,
    "provenance": 0.30,
    "mode": 0.10,
    "latency": 0.10,
    "honesty": 1.00,  # используется только для неотвечаемых
}


def score_question(q: CompetenceQuestion, metrics: list[MetricScore]) -> float:
    if not q.answerable:
        for m in metrics:
            if m.name == "honesty":
                return m.score
        return 0.0
    num = 0.0
    den = 0.0
    for m in metrics:
        if not m.applicable or m.name == "honesty":
            continue
        w = WEIGHTS.get(m.name, 0.0)
        if w <= 0:
            continue
        num += w * m.score
        den += w
    return num / den if den else 0.0


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _wmean(rs: list[QuestionResult]) -> float:
    """Средний балл, взвешенный по q.weight (структурные/контрольные вопросы
    могут весить меньше грунтованных)."""
    num = sum(r.score * r.weight for r in rs)
    den = sum(r.weight for r in rs)
    return num / den if den else 0.0


def aggregate(
    results: list[QuestionResult], pass_threshold: float, latency_target_s: float
) -> dict[str, Any]:
    done = [r for r in results if r.ok]
    overall = _wmean(done)
    passed = [r for r in done if r.score >= pass_threshold]

    # по категориям
    by_cat: dict[str, dict[str, Any]] = {}
    cats = sorted({r.category for r in results})
    for c in cats:
        rs = [r for r in done if r.category == c]
        by_cat[c] = {
            "n": len([r for r in results if r.category == c]),
            "ok": len(rs),
            "score": round(_wmean(rs), 3),
            "pass_rate": round(
                len([r for r in rs if r.score >= pass_threshold]) / len(rs), 3
            )
            if rs
            else 0.0,
        }

    # по метрикам (средние применимых)
    by_metric: dict[str, float] = {}
    for name in ("keywords", "numeric", "provenance", "mode", "latency"):
        vals = [
            m.score for r in done for m in r.metrics if m.name == name and m.applicable
        ]
        if vals:
            by_metric[name] = round(_mean(vals), 3)
    hvals = [r.score for r in results if not r.answerable and r.ok]
    if hvals:
        by_metric["honesty"] = round(_mean(hvals), 3)

    # покрытие провенансом: доля отвечаемых ответов, несущих ссылку на источник
    # (цитата / experiment_id) — прямая мера «ссылки на источники всегда есть».
    ans = [r for r in done if r.answerable]
    with_src = [r for r in ans if r.n_citations > 0 or r.n_experiment_ids > 0]
    with_docname = [r for r in ans if r.n_citations > 0]
    prov = {
        "answerable": len(ans),
        "with_source": len(with_src),
        "source_coverage": round(len(with_src) / len(ans), 3) if ans else 0.0,
        "with_citation": len(with_docname),
        "citation_coverage": round(len(with_docname) / len(ans), 3) if ans else 0.0,
    }

    lat = [r.latency_s for r in done]
    latency = {}
    if lat:
        lat_sorted = sorted(lat)
        latency = {
            "mean": round(_mean(lat), 2),
            "p50": round(statistics.median(lat), 2),
            "p95": round(
                lat_sorted[min(len(lat_sorted) - 1, int(0.95 * len(lat_sorted)))], 2
            ),
            "max": round(max(lat), 2),
            "target_s": latency_target_s,
            "within_target": len([x for x in lat if x <= latency_target_s]),
        }

    return {
        "score": round(overall, 3),
        "n_total": len(results),
        "n_ok": len(done),
        "n_error": len(results) - len(done),
        "pass_threshold": pass_threshold,
        "n_passed": len(passed),
        "pass_rate": round(len(passed) / len(done), 3) if done else 0.0,
        "by_category": by_cat,
        "by_metric": by_metric,
        "provenance": prov,
        "latency": latency,
    }
