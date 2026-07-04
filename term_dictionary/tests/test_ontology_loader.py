"""Zero-dependency smoke test for the deliverable loader against real data.

Runs with plain `pytest` — no LaBSE/numpy/network — so integration can trust
the adapter before wiring it into workers/etl.

    cd term_dictionary && python -m pytest tests/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))

from term_dictionary.ontology_loader import TermDictionary

DATA = Path(__file__).resolve().parents[1] / "data"


def _td():
    return TermDictionary(DATA)


def test_entity_ruler_patterns_present_and_shaped():
    pats = _td().entity_ruler_patterns()
    assert len(pats) > 500
    assert all("label" in p and "pattern" in p for p in pats)
    labels = {p["label"] for p in pats}
    assert {"MATERIAL", "PROCESS", "PROPERTY", "EQUIPMENT"} <= labels


def test_entity_aliases_conform_to_contract_shape():
    rows = _td().entity_aliases()
    assert len(rows) > 800
    keys = {"entity_type", "entity_id", "alias", "source"}
    assert all(keys <= set(r) for r in rows[:50])
    assert {"material", "process", "quantity_kind", "equipment"} & {
        r["entity_type"] for r in rows}


def test_entity_same_as_has_confidence_and_method():
    rows = _td().entity_same_as()
    assert rows
    methods = {r["method"] for r in rows}
    assert methods <= {"schwartz_hearst", "wikidata", "wikipedia", "contract",
                       "embedding", "lexical"}
    assert all(0.0 <= r["confidence"] <= 1.0 for r in rows)
    assert all(r["source_alias"] != r["canonical_alias"] for r in rows)


def test_cq_headwords_recognized():
    """CQ1/CQ2/CQ4 headwords must be recognizable (single or token patterns)."""
    pats = _td().entity_ruler_patterns()
    singles = {p["pattern"].lower() for p in pats if isinstance(p["pattern"], str)}
    token_terms = {
        " ".join(tok["LOWER"] for tok in p["pattern"]).lower()
        for p in pats if isinstance(p["pattern"], list)
    }
    all_terms = singles | token_terms
    for head in ["обессоливание", "desalination", "католит", "закачка",
                 "pgm", "сухой остаток", "выход по току"]:
        assert head in all_terms, f"missing CQ headword: {head}"


def test_abbreviation_lookup_resolves_variants():
    lut = _td().abbreviation_lookup()
    assert "мпг" in lut
    # every canonical expansion is non-empty
    assert all(v for v in lut.values())


def test_proposed_new_process_types_carry_evidence():
    proposed = _td().proposed_new_process_types()
    assert proposed
    p = proposed[0]
    assert {"canonical", "surface_forms", "frequency"} <= set(p)
