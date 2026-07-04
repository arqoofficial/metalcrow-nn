"""Юнит-тесты метрик — гоняются без поднятого сервиса."""

from __future__ import annotations

from benchmark import evaluators as ev
from benchmark.models import CompetenceQuestion


def _q(**gold) -> CompetenceQuestion:
    return CompetenceQuestion(id="t", category="c", question="q?", gold=gold)


def test_extract_numbers_handles_comma_superscript_and_refs():
    nums = ev.extract_numbers(
        "извлечение 95,5 % при 1250 °C, сухой остаток 1000 мг/дм³ [12]"
    )
    assert 95.5 in nums and 1250 in nums and 1000 in nums
    # библио-ссылка [12] не считается числом
    assert 12 not in nums


def test_contains_stemming():
    low = "применялось обессоливание воды обратным осмосом".lower()
    assert ev.contains(low, "обессоливание")
    assert ev.contains(low, "обессолив")  # усечённая форма
    assert ev.contains(low, "обратн")
    assert not ev.contains(low, "флотация")


def test_keywords_any_and_all():
    q = _q(
        must_include_any=["осмос", "флотация"], must_include_all=["осмос", "очистка"]
    )
    m = ev.eval_keywords(q, "очистка воды обратным осмосом")
    assert m.applicable and m.score == 1.0  # any hit (осмос) + all hit (осмос+очистка)
    # must_include_all наполовину → усредняется вниз
    half = _q(must_include_any=["осмос"], must_include_all=["осмос", "флотация"])
    assert ev.eval_keywords(half, "обратный осмос").score == 0.75
    # any полностью мимо → 0
    assert (
        ev.eval_keywords(_q(must_include_any=["флотация"]), "обратный осмос").score
        == 0.0
    )


def test_numeric_match_exact_range_and_bound():
    q = _q(
        expected_values=[
            {"label": "a", "value": 95, "op": "=", "unit": "%", "tol": 1},
            {"label": "b", "value": 200, "op": "range", "value2": 300, "unit": "мг/л"},
            {"label": "c", "value": 1000, "op": "<=", "unit": "мг/дм3"},
        ]
    )
    text = "извлечение 95,4 %, сульфаты 250 мг/л, сухой остаток не более 1000 мг/дм³"
    m = ev.eval_numeric(q, text)
    assert m.score == 1.0
    # ни одного значения
    assert ev.eval_numeric(q, "ничего числового тут нет про воду").score == 0.0


def test_numeric_inapplicable_without_expected_values():
    assert ev.eval_numeric(_q(), "любой текст").applicable is False


def test_patent_detection():
    assert ev.has_patent("описан в патенте RU 2782420 C1")
    assert ev.has_patent("а.с. 129820 СССР")
    assert not ev.has_patent("обычный текст без изобретений")
    assert "RU 2782420 C1".replace(" ", "") in ev.patent_numbers(
        "патент RU 2782420 C1"
    ).__str__().replace(" ", "")


def test_provenance_components_and_patent():
    q = _q(
        expected_sources=["Методы очистки"],
        expects_patent=True,
        min_numeric_values=3,
        min_citations=1,
    )
    flat = {
        "text": "по данным «Методы очистки шахтных вод» удаление 1500-2500 мг/л; "
        "патент RU 2782420 C1\n— источник: «...»",
        "n_numeric": 4,
        "n_citations": 1,
        "n_experiment_ids": 0,
        "has_patent": True,
    }
    m = ev.eval_provenance(q, flat)
    assert m.applicable and m.score > 0.9


def test_provenance_inapplicable_when_no_expectations():
    q = _q(must_include_any=["штейн"])  # структурный: провенанс не ждём
    flat = {
        "text": "штейн",
        "n_numeric": 0,
        "n_citations": 0,
        "n_experiment_ids": 0,
        "has_patent": False,
    }
    assert ev.eval_provenance(q, flat).applicable is False


def test_honesty_no_data_vs_hallucination():
    q = CompetenceQuestion(
        id="t",
        category="control",
        question="q?",
        answerable=False,
        gold={"expect_no_data": True, "forbid_include": ["3422"]},
    )
    good = {"text": "По запросу ничего не найдено в корпусе."}
    bad = {"text": "Температура плавления вольфрама 3422 °C."}
    assert ev.eval_honesty(q, good).score == 1.0
    assert ev.eval_honesty(q, bad).score == 0.0


def test_flatten_answer_counts():
    payload = {
        "summary": "Найдено 2 значения",
        "claims": [
            {
                "text": "извлечение 95 %\n— источник: «цитата»",
                "experiment_ids": ["a", "b"],
            },
            {"text": "патент RU 2782420 C1", "experiment_ids": []},
        ],
        "tools_used": ["ontology:evidence"],
        "mode_used": "ontology",
    }
    flat = ev.flatten_answer(payload)
    assert flat["mode_used"] == "ontology"
    assert flat["n_experiment_ids"] == 2
    assert flat["n_citations"] == 1
    assert flat["has_patent"] is True
    assert flat["n_numeric"] >= 2


def test_latency_metric():
    assert ev.eval_latency(3.0, 5.0).score == 1.0
    assert ev.eval_latency(8.0, 5.0).score == 0.5
    assert ev.eval_latency(60.0, 5.0).score < 0.5
