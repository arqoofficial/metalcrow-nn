"""Wikidata harvest — pure-logic tests (no network).

Only the offline transforms are exercised here: surface-form dedup, must-link
star edges, label/alias collection, anchor-file parsing, glossary flattening.
The live HTTP paths are covered by the on-VM harvest run, not unit tests.
"""

from pathlib import Path

from term_dict.wikidata import (
    WdConcept,
    _dedup,
    concepts_to_glossary_rows,
    load_anchor_file,
    load_must_link,
    write_glossary,
)


def _nickel() -> WdConcept:
    return WdConcept(
        qid="Q883", label="MATERIAL",
        canonical_en="nickel", canonical_ru="никель",
        en=["nickel", "Ni", "nickel"], ru=["никель", "Ni"])


def test_surface_forms_dedup_case_insensitive():
    c = _nickel()
    forms = c.surface_forms()
    terms = [t for t, _ in forms]
    # "nickel" appears twice in en, "Ni" in both en and ru -> deduped once each.
    assert terms.count("nickel") == 1
    assert terms.count("Ni") == 1
    langs = {t: lang for t, lang in forms}
    assert langs["nickel"] == "en" and langs["никель"] == "ru"


def test_must_link_star_from_canonical():
    c = _nickel()
    pairs = c.must_link_pairs()
    # every edge starts at the canonical EN head; no self-edge.
    assert all(a == "nickel" for a, _ in pairs)
    assert ("nickel", "никель") in pairs
    assert all(a != b for a, b in pairs)


def test_dedup_drops_empties_and_case_dups_order_stable():
    # label first, empty dropped, case-insensitive dup dropped, order kept.
    out = _dedup(["copper", "", "Cu", "COPPER", "cu"])
    assert out == ["copper", "Cu"]


def test_concepts_to_glossary_rows_carry_label_and_qid():
    rows = concepts_to_glossary_rows([_nickel()])
    assert {r["label"] for r in rows} == {"MATERIAL"}
    assert {r["qid"] for r in rows} == {"Q883"}
    assert {"nickel", "никель", "Ni"} <= {r["term"] for r in rows}


def test_load_anchor_file_skips_comments_and_short_rows(tmp_path: Path):
    p = tmp_path / "anchors.tsv"
    p.write_text(
        "# header comment\n"
        "Q883\tnickel\tникель\tMATERIAL\t# note\n"
        "\n"
        "?\tbad\t\tPROCESS\n"          # non-Q qid -> skipped
        "Q123\tflotation\tфлотация\tprocess\n",  # lower label -> upper-cased
        encoding="utf-8")
    anchors = load_anchor_file(p)
    assert ("Q883", "MATERIAL") in anchors
    assert ("Q123", "PROCESS") in anchors
    assert all(qid.startswith("Q") for qid, _ in anchors)
    assert len(anchors) == 2


def test_write_and_reload_must_link_roundtrip(tmp_path: Path):
    gloss = tmp_path / "g.jsonl"
    pairs = tmp_path / "ml.json"
    write_glossary([_nickel()], out_path=gloss, pairs_path=pairs)
    assert gloss.exists()
    loaded = load_must_link(pairs)
    assert ("nickel", "никель") in loaded
