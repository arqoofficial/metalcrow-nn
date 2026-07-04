# -*- coding: utf-8 -*-
"""Тесты канонизации величин (слои правил, без LLM)."""
from ontology.extract.quantities import canonize


def test_junk_rejected():
    for junk in ("string", "verbatim-string", "performance", ""):
        assert canonize(junk).kind is None


def test_exact_and_english_aliases():
    assert canonize("temperature").kind == "temperature"
    assert canonize("corrosion_rate").kind == "corrosion_rate"
    assert canonize("коэффициенты_запаса_устойчивости").kind == "safety_factor"
    assert canonize("овп").kind == "redox_potential"


def test_kind_plus_subject():
    c = canonize("извлечение никеля в медный концентрат")
    assert c.kind == "recovery_degree" and c.subject == "Ni"
    c = canonize("nickel_extraction_to_tailings")
    assert c.kind == "recovery_degree" and c.subject == "Ni"
    c = canonize("содержание кислорода в дутье")
    assert c.kind == "element_content" and c.subject == "O"
    c = canonize("fe_content")
    assert c.kind == "element_content" and c.subject == "Fe"


def test_compressive_not_tensile():
    assert canonize("предел_прочности_при_одноосном_сжатии").kind == "compressive_strength"
    assert canonize("предел_прочности_на_растяжение").kind == "tensile_strength"


def test_pressure_not_stress():
    assert canonize("ignition_onset_pressure").kind == "pressure"
    # МПа без имени-подсказки — честный unresolved, а не угадывание
    assert canonize("параметр_x", "МПа").kind is None


def test_qualitative_bucket():
    for raw in ("appearance_of_sinters", "локализация_и_форма_потери_устойчивости",
                "foaming_and_pulp_discharge"):
        c = canonize(raw)
        assert c.kind == "qualitative_observation"


def test_hardness_unit_hint():
    c = canonize("твердость_осадка", "HV30")
    assert c.kind == "hardness"


def test_subject_normalization():
    from ontology.extract.quantities import normalize_subject
    assert normalize_subject("никеля") == "Ni"
    assert normalize_subject("nickel") == "Ni"
    assert normalize_subject("Au, Ag и МПГ") == "Au+Ag+PGM"
    assert normalize_subject("драгоценных металлов") == "precious"
    assert normalize_subject("цветных металлов") == "non-ferrous"
    assert normalize_subject("silicate matrix") == "silicate matrix"


def test_subject_normalized_only_for_element_kinds():
    c = canonize("извлечение никеля в медный концентрат")
    assert c.kind == "recovery_degree" and c.subject == "Ni"
    # поток серной кислоты — предмет НЕ сводится к элементу
    c = canonize("расход серной кислоты")
    assert c.kind == "flow_rate" and c.subject == "серной кислоты"
