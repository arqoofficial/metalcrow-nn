"""Tests for the ontology-contract export adapter + loader."""

import json

from term_dict.ontology_export import build_exports, write_exports
from term_dict.ontology_loader import TermDictionary

_SNAPSHOT = {
    "process_types": {
        "electroextraction": {"enum": "ELECTROEXTRACTION",
                              "aliases": ["электроэкстракция", "electrowinning"]},
        "leaching": {"enum": "LEACHING", "aliases": ["выщелачивание", "leaching"]},
    },
    "quantity_kinds": {
        "recovery_degree": {"unit_dim": "ratio", "aliases": ["извлечение"]},
    },
}

_SYNMAP = [
    {  # PROCESS resolving to an existing enum member, with a NEW alias.
        "concept_id": "C1", "canonical": "electrowinning", "label": "PROCESS",
        "surface_forms": ["electrowinning", "электроэкстракция", "ЭЭ"],
        "members": [
            {"term": "electrowinning", "sources": ["wikidata"]},
            {"term": "электроэкстракция", "sources": ["contract"]},
            {"term": "ЭЭ", "sources": ["schwartz_hearst"]},
        ],
        "same_as_edges": [
            {"a": "electrowinning", "b": "электроэкстракция", "confidence": 1.0, "method": "contract"},
            {"a": "electrowinning", "b": "ЭЭ", "confidence": 1.0, "method": "schwartz_hearst"},
        ],
    },
    {  # PROCESS overflowing the enum -> proposed-new.
        "concept_id": "C2", "canonical": "comminution", "label": "PROCESS",
        "surface_forms": ["comminution", "измельчение"],
        "members": [
            {"term": "comminution", "sources": ["yake"]},
            {"term": "измельчение", "sources": ["yake"]},
        ],
        "same_as_edges": [
            {"a": "comminution", "b": "измельчение", "confidence": 0.83, "method": "embedding"},
        ],
    },
    {  # MATERIAL concept -> entity_aliases + same_as only.
        "concept_id": "C3", "canonical": "Ni", "label": "MATERIAL",
        "surface_forms": ["Ni", "nickel", "никель"],
        "members": [
            {"term": "Ni", "sources": ["wikidata"]},
            {"term": "nickel", "sources": ["wikidata"]},
            {"term": "никель", "sources": ["seed"]},
        ],
        "same_as_edges": [
            {"a": "nickel", "b": "Ni", "confidence": 1.0, "method": "wikidata"},
            {"a": "nickel", "b": "никель", "confidence": 1.0, "method": "wikidata"},
        ],
    },
]


def test_entity_aliases_cover_every_surface_form():
    ex = build_exports(_SYNMAP, _SNAPSHOT)
    aliases = ex["entity_aliases"]
    assert len(aliases) == 8  # 3 + 2 + 3
    ni = [a for a in aliases if a["entity_id"] == "C3"]
    assert {a["alias"] for a in ni} == {"Ni", "nickel", "никель"}
    assert all(a["entity_type"] == "material" for a in ni)


def test_process_alias_enrichment_adds_only_new():
    ex = build_exports(_SYNMAP, _SNAPSHOT)
    enr = ex["process_alias_enrichment"]
    # "электроэкстракция"/"electrowinning" already in the snapshot; only "ЭЭ" is new.
    assert "electroextraction" in enr
    assert "ЭЭ" in enr["electroextraction"]
    assert "electrowinning" not in enr["electroextraction"]


def test_overflow_process_becomes_proposed_new():
    ex = build_exports(_SYNMAP, _SNAPSHOT)
    proposed = ex["proposed_new_process_types"]
    assert any(p["canonical"] == "comminution" for p in proposed)
    p = next(p for p in proposed if p["canonical"] == "comminution")
    assert "измельчение" in p["surface_forms"] and p["frequency"] == 2


def test_enrichment_skips_alias_belonging_to_another_member():
    # A concept resolving to "annealing" that also contains "выщелачивание"
    # (a leaching alias) must NOT enrich annealing with it.
    synmap = [{
        "concept_id": "CX", "canonical": "annealing", "label": "PROCESS",
        "surface_forms": ["annealing", "отжиг", "выщелачивание"],
        "members": [{"term": t, "sources": ["yake"]} for t in
                    ["annealing", "отжиг", "выщелачивание"]],
        "same_as_edges": [],
    }]
    snap = {"process_types": {
        "annealing": {"enum": "ANNEALING", "aliases": ["отжиг", "annealing"]},
        "leaching": {"enum": "LEACHING", "aliases": ["выщелачивание", "leaching"]},
    }, "quantity_kinds": {}}
    enr = build_exports(synmap, snap)["process_alias_enrichment"]
    assert "выщелачивание" not in enr.get("annealing", [])


def test_enrichment_skips_false_friend():
    synmap = [{
        "concept_id": "CY", "canonical": "extrusion", "label": "PROCESS",
        "surface_forms": ["extrusion", "экструзия", "экстракция"],
        "members": [{"term": t, "sources": ["yake"]} for t in
                    ["extrusion", "экструзия", "экстракция"]],
        "same_as_edges": [],
    }]
    snap = {"process_types": {
        "extrusion": {"enum": "EXTRUSION", "aliases": ["экструзия", "extrusion"]}},
        "quantity_kinds": {}}
    enr = build_exports(synmap, snap)["process_alias_enrichment"]
    assert "экстракция" not in enr.get("extrusion", [])


def test_multiword_resolves_by_token_to_existing_member():
    # "Froth flotation" should enrich the existing "flotation" member, not be
    # proposed as a new type.
    synmap = [{
        "concept_id": "CZ", "canonical": "Froth flotation", "label": "PROCESS",
        "surface_forms": ["Froth flotation"],
        "members": [{"term": "Froth flotation", "sources": ["wikipedia"]}],
        "same_as_edges": [],
    }]
    snap = {"process_types": {
        "flotation": {"enum": "FLOTATION", "aliases": ["флотация", "flotation"]}},
        "quantity_kinds": {}}
    ex = build_exports(synmap, snap)
    assert not ex["proposed_new_process_types"]
    assert "Froth flotation" in ex["process_alias_enrichment"]["flotation"]


def test_same_as_carries_method_and_confidence():
    ex = build_exports(_SYNMAP, _SNAPSHOT)
    sa = ex["entity_same_as"]
    ee = [r for r in sa if r["source_alias"] == "ЭЭ"]
    assert ee and ee[0]["method"] == "schwartz_hearst" and ee[0]["confidence"] == 1.0
    # canonical form itself is never its own same_as source.
    assert not [r for r in sa if r["source_alias"] == r["canonical_alias"]]


def test_loader_roundtrip(tmp_path):
    ex = build_exports(_SYNMAP, _SNAPSHOT)
    write_exports(ex, tmp_path / "ontology")
    (tmp_path / "abbreviations.json").write_text(json.dumps(
        [{"acronym": "ЭЭ", "canonical_expansion": "электроэкстракция",
          "expansion_variants": ["электроэкстракции"]}]), encoding="utf-8")
    td = TermDictionary(tmp_path)
    assert len(td.entity_aliases()) == 8
    assert td.process_alias_enrichment()["electroextraction"] == ["ЭЭ"]
    assert td.abbreviation_lookup()["электроэкстракции"] == "электроэкстракция"
    assert td.entity_ruler_patterns() == []  # tolerates missing file
