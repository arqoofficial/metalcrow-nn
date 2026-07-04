"""Tests for the EntityRuler precision filter."""

from term_dict.pattern_filter import (
    dedup_declension,
    is_junk_surface_form,
    junk_reason,
)


def test_brand_code_alias_dropped():
    assert junk_reason("Tatum-T", "MATERIAL", {"wikidata"}) == "brand_code"
    assert is_junk_surface_form("Tatum-T") is True
    # element symbols and real terms are never treated as junk surface forms.
    assert is_junk_surface_form("Ni") is False
    assert is_junk_surface_form("фильтр-пресс") is False
    assert is_junk_surface_form("SX/EW") is False


def test_ru_verbal_fragment_dropped():
    assert junk_reason("получения сульфата никеля", "MATERIAL") == "ru_verbal_fragment"
    assert junk_reason("извлечения мпг", "PROCESS") == "ru_verbal_fragment"


def test_dangling_modifier_dropped():
    assert junk_reason("сульфата никеля высокой", "MATERIAL") == "dangling_modifier"


def test_smart_quote_fragment_dropped():
    assert junk_reason("‘top service’", "PROPERTY") == "smart_quote_fragment"


def test_generic_english_singleton_dropped():
    assert junk_reason("extraction", "PROCESS") == "generic_english"
    assert junk_reason("purification", "PROCESS") == "generic_english"
    # multiword domain phrase with a generic head is kept.
    assert junk_reason("solvent extraction", "PROCESS") is None


def test_bare_short_token_dropped_only_without_trust():
    assert junk_reason("ЭР", "PROCESS", set()) == "bare_short_token"
    assert junk_reason("ЭР", "PROCESS", {"yake"}) == "bare_short_token"
    # element symbol / SH acronym carries a trusted source → kept.
    assert junk_reason("Ni", "MATERIAL", {"wikidata"}) is None
    assert junk_reason("ВК", "PROCESS", {"schwartz_hearst"}) is None


def test_single_char_element_symbol_dropped():
    assert junk_reason("C", "MATERIAL", {"wikidata"}) == "single_char"
    assert junk_reason("I", "MATERIAL", {"wikidata"}) == "single_char"
    # two-char element symbols are specific enough to keep.
    assert junk_reason("Ni", "MATERIAL", {"wikidata"}) is None


def test_good_terms_survive():
    assert junk_reason("сульфат никеля", "MATERIAL") is None
    assert junk_reason("обессоливание", "PROCESS") is None
    assert junk_reason("выход по току", "PROPERTY") is None
    assert junk_reason("пористость", "PROPERTY") is None  # -ость not a dangling adj


def test_declension_dedup_collapses_variants():
    terms = [
        ("раствор", "MATERIAL", {"seed"}),
        ("раствора", "MATERIAL", {"yake"}),
        ("извлечение", "PROCESS", {"seed"}),
        ("извлечения", "PROCESS", {"yake"}),
        ("медь", "MATERIAL", {"seed"}),      # must NOT merge with медный
        ("медный", "MATERIAL", {"yake"}),
    ]
    kept, dropped = dedup_declension(terms)
    kept_terms = {t for t, _l, _s in kept}
    assert "раствор" in kept_terms and "раствора" not in kept_terms
    assert "извлечение" in kept_terms and "извлечения" not in kept_terms
    # медь (4 chars) is below the safe stem length → untouched, both survive.
    assert "медь" in kept_terms and "медный" in kept_terms


def test_declension_keeps_distinct_words():
    # Distinct concepts sharing a short prefix must never collapse.
    terms = [("флотация", "PROCESS", {"seed"}), ("флокуляция", "PROCESS", {"seed"})]
    kept, dropped = dedup_declension(terms)
    assert len(kept) == 2 and not dropped
