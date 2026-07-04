"""Tests for canonical_material/canonical_process, incl. the term_dictionary
synonym_map.json fallback."""

from science_kg.nlp.normalizer import canonical_material, canonical_process


def test_canonical_material_hand_written_alias_wins():
    """ВТ6 is in the hand-written _MATERIAL_ALIASES — must take priority over
    whatever term_dictionary's synonym_map.json might also say about it."""
    assert canonical_material("ВТ6") == "Ti-6Al-4V"


def test_canonical_material_synonym_map_fallback():
    """"опреснение" (RU for desalination) is not in the hand-written alias
    table — this is a PROCESS case, not MATERIAL, so plain
    canonical_material should NOT resolve it (falls through to the input
    unchanged); the PROCESS-side equivalent is tested separately below."""
    assert canonical_material("опреснение") == "опреснение"


def test_canonical_process_synonym_map_fallback():
    """"опреснение" is a RU surface form of the "desalination" PROCESS concept
    in term_dictionary's synonym_map.json (needs_review: false) — canonical_process
    should resolve it via the fallback lookup."""
    assert canonical_process("опреснение") == "desalination"


def test_canonical_process_hand_written_alias_still_wins():
    assert canonical_process("вакуум") == "vacuum"


def test_canonical_process_unknown_text_passthrough():
    assert canonical_process("some unrelated regime text") == "some unrelated regime text"


def test_canonical_material_formula_case_not_collapsed_by_synonym_map():
    """Regression: synonym_map.json has a MATERIAL concept canonical="tin" (the
    metal, RU "олово"). Lowercasing "TiN" (titanium nitride, a real chemical
    formula distinct from tin/Sn) for the fallback lookup used to silently
    rewrite it to "tin" — losing the compound's identity. Formula-looking text
    (internal capital after position 0) must skip the synonym fallback."""
    assert canonical_material("TiN") == "TiN"
