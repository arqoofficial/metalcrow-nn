"""Tests for the Ceder-based conditions extractor."""

from science_kg.nlp.ceder_extractor import extract_ceder_relations
from science_kg.models import RelationType, EntityType


def _rels(text: str):
    return extract_ceder_relations(text, doc_id="test")


# ── Temperature ───────────────────────────────────────────────────────────────


def test_temperature_basic():
    rels = _rels("Ti-6Al-4V was heated at 850°C for sintering.")
    regimes = {r.source for r in rels}
    assert any("850" in s for s in regimes), f"Expected 850°C in {regimes}"


def test_temperature_relation_type():
    rels = _rels("Ti-6Al-4V was annealed at 950°C.")
    assert all(r.relation == RelationType.USES_MATERIAL for r in rels)
    assert all(r.source_type == EntityType.PROCESS for r in rels)
    assert all(r.target_type == EntityType.MATERIAL for r in rels)


def test_temperature_ocr_variant():
    """'900oC' is an OCR artifact for 900°C — normalizer should fix it."""
    rels = _rels("Ti-6Al-4V specimens were treated at 900oC.")
    # should extract a regime containing "900"
    regimes = {r.source for r in rels}
    assert any("900" in s for s in regimes), f"Got {regimes}"


def test_temperature_kelvin():
    rels = _rels("The alloy Ti-6Al-4V was heated to 1073K.")
    regimes = {r.source for r in rels}
    assert any("1073" in s for s in regimes), f"Got {regimes}"


# ── Time ──────────────────────────────────────────────────────────────────────


def test_time_hours():
    rels = _rels("Ti-6Al-4V was held for 2 h at temperature.")
    regimes = {r.source for r in rels}
    assert any("2" in s for s in regimes), f"Got {regimes}"


def test_time_minutes():
    rels = _rels("NiTi samples were aged for 30 min.")
    regimes = {r.source for r in rels}
    assert any("30" in s for s in regimes), f"Got {regimes}"


def test_time_overnight():
    rels = _rels("Ti-6Al-4V was dried overnight in vacuum.")
    regimes = {r.source for r in rels}
    assert any("overnight" in s for s in regimes), f"Got {regimes}"


# ── Atmosphere ────────────────────────────────────────────────────────────────


def test_atmosphere_argon():
    rels = _rels("Ti-6Al-4V was sintered in argon atmosphere.")
    regimes = {r.source for r in rels}
    assert "argon" in regimes, f"Got {regimes}"


def test_atmosphere_vacuum():
    rels = _rels("NiTi was annealed under vacuum at 500°C.")
    regimes = {r.source for r in rels}
    assert "vacuum" in regimes, f"Got {regimes}"


def test_atmosphere_air():
    rels = _rels("Ti-6Al-4V specimens were cooled in air.")
    regimes = {r.source for r in rels}
    assert "air" in regimes, f"Got {regimes}"


# ── No material → empty ───────────────────────────────────────────────────────


def test_no_material_returns_empty():
    rels = _rels("The sample was heated at 850°C for 2 h in argon.")
    assert rels == [], f"Expected empty, got {rels}"


def test_empty_text_returns_empty():
    assert extract_ceder_relations("", "test") == []


# ── Target is the correct material ───────────────────────────────────────────


def test_target_material_name():
    rels = _rels("Ti-6Al-4V was processed at 900°C for 1 h in argon.")
    for r in rels:
        assert "Ti-6Al-4V" in r.target or "ti-6al-4v" in r.target.lower(), (
            f"Expected Ti-6Al-4V as target, got {r.target!r}"
        )


# ── Deduplication ─────────────────────────────────────────────────────────────


def test_deduplication():
    text = "Ti-6Al-4V was heated at 850°C. Ti-6Al-4V was again treated at 850°C."
    rels = _rels(text)
    sources = [r.source for r in rels if "850" in r.source]
    assert len(sources) == 1, f"Expected 1 deduplicated entry, got {sources}"


# ── Combined sentence ─────────────────────────────────────────────────────────


def test_combined_conditions():
    rels = _rels("Ti-6Al-4V was annealed at 850°C for 2 h in argon atmosphere.")
    regimes = {r.source for r in rels}
    assert any("850" in s for s in regimes), "Missing temperature"
    assert any("2" in s for s in regimes), "Missing time"
    assert "argon" in regimes, "Missing atmosphere"
