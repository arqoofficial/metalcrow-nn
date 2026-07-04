"""Tests for the abbreviation-consolidation subsystem."""

from term_dict.abbreviations import extract_abbreviations
from term_dict.abbreviations import _expansion_key, _initial_coverage


def _by_acronym(abbrevs):
    return {a.acronym: a for a in abbrevs}


def test_declension_variants_collapse_to_one_record():
    docs = [
        "Исследован процесс электроэкстракции (ЭЭ) никеля.",
        "Метод электроэкстракция (ЭЭ) широко применяется.",
        "Параметры электроэкстракцию (ЭЭ) контролируют.",
    ]
    ab = _by_acronym(extract_abbreviations(docs))
    assert "ЭЭ" in ab
    rec = ab["ЭЭ"]
    # all three inflected forms grouped under one abbreviation record.
    assert len(rec.expansion_variants) >= 3
    assert rec.n_docs == 3
    # canonical stems to the same lemma as the variants.
    assert _expansion_key(rec.canonical_expansion).startswith("электроэкстракц")


def test_confidence_rises_with_corpus_support():
    once = extract_abbreviations(
        ["Пирометаллургическая плавка (ПМП) идёт в печи."])
    many = extract_abbreviations([
        "Пирометаллургическая плавка (ПМП) — стадия один.",
        "Пирометаллургическая плавка (ПМП) — стадия два.",
    ])
    c1 = _by_acronym(once)["ПМП"].confidence
    c2 = _by_acronym(many)["ПМП"].confidence
    assert c2 > c1


def test_initial_coverage_full_vs_partial():
    assert _initial_coverage("POX", "pressure oxidation") < 1.0  # 3 letters, 2 words
    assert _initial_coverage("НФО", "насосно фильтровальное отделение") == 1.0


def test_english_acronym_lang_detected():
    ab = _by_acronym(extract_abbreviations(
        ["The feed underwent pressure oxidation (POX) twice."]))
    assert "POX" in ab
    assert ab["POX"].lang == "en"


def test_junk_abbreviations_filtered():
    from term_dict.abbreviations import _is_junk_abbrev
    assert _is_junk_abbrev("Mining", "Mining")       # identity
    assert _is_junk_abbrev("PRU", "Перу")            # country
    assert _is_junk_abbrev("S/", "sin α")            # formula fragment
    assert not _is_junk_abbrev("МПГ", "металлы платиновой группы")
    assert not _is_junk_abbrev("KML", "Katanga Mining Limited")
