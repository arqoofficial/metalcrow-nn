"""Tests for the Schwartz-Hearst acronym extractor (no model load)."""

from term_dict import schwartz_hearst as sh


def _pairs(text):
    return {p.short_form: p.long_form for p in sh.extract_pairs(text)}


def test_english_long_form_then_short():
    p = _pairs("The feed was treated by pressure oxidation (POX) in an autoclave.")
    assert p["POX"].lower() == "pressure oxidation"


def test_russian_long_form_then_short():
    p = _pairs("Исследован процесс электроэкстракции (ЭЭ) никеля.")
    assert "ЭЭ" in p
    assert "электроэкстракц" in p["ЭЭ"].lower()


def test_multiword_russian_acronym():
    p = _pairs("Насосно-фильтровальное отделение (НФО) осветляло пульпу.")
    assert p["НФО"].lower().startswith("насосно")


def test_slash_acronym():
    p = _pairs("Solvent extraction / electrowinning (SX/EW) was applied.")
    assert "SX/EW" in p


def test_invalid_pair_rejected():
    # Short form chars not a subsequence of the left context -> no pair.
    p = _pairs("The reactor ran hot (XYZ) all day.")
    assert "XYZ" not in p


def test_lowercase_word_not_acronym():
    # A plain lowercase parenthetical is not an acronym.
    p = _pairs("The process (около сорока минут) completed.")
    assert p == {}


def test_chemistry_formula_rejected():
    # Reaction equations / chemical formulas must not be mistaken for acronym
    # definitions — the subsequence test happily matches SO4 against H2SO4.
    assert "SO4" not in _pairs("окисление по реакции H2SO4 + 1/2 O2 = Fe2 (SO4)")
    assert "OH" not in _pairs("гидроксид меди Co(OH)3, а железо (OH) осаждается")
    assert "CN" not in _pairs("комплекс Pt(CN)4 2- образует (CN) прочную связь")


def test_single_word_expansion_survives_chemistry_guard():
    # The guard must not reject legitimate single-word natural-language
    # expansions (regression: an over-strict ">=2 words" rule killed these).
    p = _pairs("Применяли флотацию (Фл) для обогащения.")
    assert "Фл" in p and "флотаци" in p["Фл"].lower()


def test_dedup_first_wins():
    text = ("pressure oxidation (POX) ... later the pressurized ordinary "
            "xenon (POX) test")
    pairs = sh.extract_pairs_from_docs([text, text])
    got = [p for p in pairs if p.short_form == "POX"]
    assert len(got) == 1
