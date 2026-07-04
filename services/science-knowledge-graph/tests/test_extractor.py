"""Unit tests for NLP extraction — no Neo4j required."""

import pytest
from science_kg.nlp.pipeline import build_pipeline, detect_language
from science_kg.nlp.extractor import extract_entities, extract_relations
from science_kg.models import EntityType


@pytest.fixture(scope="module")
def nlp():
    return build_pipeline("ru_core_news_sm")


@pytest.fixture(scope="module")
def nlp_en():
    return build_pipeline("en_core_sci_sm")


# ── Russian entity extraction ─────────────────────────────────────────────────


class TestEntityExtractionRU:
    def test_material_detected(self, nlp):
        doc = nlp("Сплав ВТ6 исследовали при нагреве.")
        labels = {e.label for e in extract_entities(doc)}
        assert EntityType.MATERIAL in labels

    def test_regime_temperature(self, nlp):
        doc = nlp("Отжиг проводили при 850°C.")
        ents = extract_entities(doc)
        regime_texts = [e.text for e in ents if e.label == EntityType.PROCESS]
        assert any("850" in t for t in regime_texts)

    def test_property_detected(self, nlp):
        doc = nlp("Прочность материала возросла до 980 МПа.")
        ents = extract_entities(doc)
        labels = {e.label for e in ents}
        assert EntityType.PROPERTY in labels

    def test_multiple_entities(self, nlp):
        doc = nlp("Закалка Ti-6Al-4V при 950°C повысила твёрдость до 42 HRC.")
        ents = extract_entities(doc)
        types = {e.label for e in ents}
        assert EntityType.MATERIAL in types
        assert EntityType.PROCESS in types

    def test_source_doc_propagated(self, nlp):
        doc = nlp("Сплав ВТ6 при 850°C.")
        ents = extract_entities(doc, source_doc="paper-001")
        assert all(e.source_doc == "paper-001" for e in ents)


# ── Pattern fixes ─────────────────────────────────────────────────────────────


class TestPatternFixes:
    def test_mpa_not_material(self, nlp_en):
        doc = nlp_en("Tensile strength reached 1120 MPa.")
        mat_texts = [
            e.text for e in extract_entities(doc) if e.label == EntityType.MATERIAL
        ]
        assert "MPa" not in mat_texts

    def test_hv5_not_material(self, nlp_en):
        doc = nlp_en("Hardness was 290 HV5.")
        mat_texts = [
            e.text for e in extract_entities(doc) if e.label == EntityType.MATERIAL
        ]
        assert "HV5" not in mat_texts

    def test_lpbf_not_material(self, nlp_en):
        doc = nlp_en("The alloy was processed by LPBF.")
        mat_texts = [
            e.text for e in extract_entities(doc) if e.label == EntityType.MATERIAL
        ]
        assert "LPBF" not in mat_texts

    def test_ti555211_detected(self, nlp_en):
        doc = nlp_en("The Ti555211 alloy was heat treated at 820°C.")
        mat_texts = [
            e.text for e in extract_entities(doc) if e.label == EntityType.MATERIAL
        ]
        assert "Ti555211" in mat_texts

    def test_ti_hyphen_alloy_detected(self, nlp_en):
        doc = nlp_en("Ti-24Nb-4Zr-8Sn was processed at 750°C.")
        mat_texts = [
            e.text for e in extract_entities(doc) if e.label == EntityType.MATERIAL
        ]
        assert "Ti-24Nb-4Zr-8Sn" in mat_texts

    def test_ti_numeric_detected(self, nlp_en):
        doc = nlp_en("Ti2448 achieved elongation of 22%.")
        mat_texts = [
            e.text for e in extract_entities(doc) if e.label == EntityType.MATERIAL
        ]
        assert "Ti2448" in mat_texts

    def test_chemical_formula_detected(self, nlp_en):
        doc = nlp_en("A TiN coating was applied.")
        mat_texts = [
            e.text for e in extract_entities(doc) if e.label == EntityType.MATERIAL
        ]
        assert "TiN" in mat_texts


# ── English entity extraction ─────────────────────────────────────────────────


class TestEntityExtractionEN:
    def test_english_material(self, nlp_en):
        doc = nlp_en("The Ti-24Nb-4Zr-8Sn alloy was solution treated at 750°C.")
        mat_texts = [
            e.text for e in extract_entities(doc) if e.label == EntityType.MATERIAL
        ]
        assert "Ti-24Nb-4Zr-8Sn" in mat_texts

    def test_english_regime(self, nlp_en):
        doc = nlp_en("Solution treatment at 820°C for 2 hours was applied.")
        ents = extract_entities(doc)
        regime_texts = [e.text for e in ents if e.label == EntityType.PROCESS]
        assert any("820" in t for t in regime_texts)

    def test_english_property(self, nlp_en):
        doc = nlp_en("Tensile strength increased after aging.")
        ents = extract_entities(doc)
        labels = {e.label for e in ents}
        assert EntityType.PROPERTY in labels

    def test_english_value(self, nlp_en):
        doc = nlp_en("The alloy achieved tensile strength of 1404 MPa.")
        val_texts = [
            e.text for e in extract_entities(doc) if e.label == EntityType.PROPERTY
        ]
        assert any("1404" in t for t in val_texts)


# ── Relation extraction ───────────────────────────────────────────────────────


class TestRelationExtraction:
    def test_no_duplicate_relations(self, nlp):
        doc = nlp(
            "Нагрев ВТ6 при 900°C увеличил твёрдость. Нагрев ВТ6 при 900°C увеличил твёрдость."
        )
        relations = extract_relations(doc)
        keys = [(r.source, r.relation, r.target) for r in relations]
        assert len(keys) == len(set(keys))

    def test_source_doc_in_relation(self, nlp):
        doc = nlp("Отжиг при 850°C увеличил прочность.")
        relations = extract_relations(doc, source_doc="paper-001")
        for rel in relations:
            assert rel.source_doc == "paper-001"

    def test_no_fallback_false_relations(self, nlp_en):
        doc = nlp_en(
            "Ti555211 showed high strength. Ti-24Nb-4Zr-8Sn showed low modulus."
        )
        relations = extract_relations(doc)
        sources = {r.source for r in relations}
        targets = {r.target for r in relations}
        assert not (
            ("Ti555211" in sources and "Ti-24Nb-4Zr-8Sn" in targets)
            or ("Ti-24Nb-4Zr-8Sn" in sources and "Ti555211" in targets)
        )


class TestPassiveVoice:
    def test_passive_nsubjpass_becomes_target(self, nlp_en):
        # "hardness was increased" → hardness is semantic target
        doc = nlp_en("The hardness was increased after treatment at 820°C.")
        relations = extract_relations(doc)
        targets = {r.target for r in relations}
        assert "hardness" in targets

    def test_passive_obl_regime_becomes_source(self, nlp_en):
        # "was treated at 820°C" → 820°C should appear as source
        doc = nlp_en("Ti-6Al-4V was treated at 820°C for 2 hours.")
        relations = extract_relations(doc)
        sources = {r.source for r in relations}
        assert any("820" in s for s in sources)

    def test_passive_no_agent_no_false_source(self, nlp_en):
        # Passive with no agent and no obl entity → no relation emitted
        doc = nlp_en("The optimal properties were achieved.")
        relations = extract_relations(doc)
        # No entities in sentence → no relations
        assert len(relations) == 0

    def test_passive_by_agent(self, nlp_en):
        # "hardness was increased by aging at 820°C" → 820°C source, hardness target
        doc = nlp_en("The hardness was increased by heat treatment at 820°C.")
        relations = extract_relations(doc)
        targets = {r.target for r in relations}
        # hardness should be target (semantic object in passive)
        assert "hardness" in targets


# ── Language detection ────────────────────────────────────────────────────────


class TestLanguageDetection:
    def test_russian_text(self):
        assert (
            detect_language(
                "Отжиг сплава ВТ6 при температуре 850°C увеличил прочность."
            )
            == "ru"
        )

    def test_english_text(self):
        assert (
            detect_language("The Ti555211 alloy was heat treated at 820°C for 2 hours.")
            == "en"
        )

    def test_mixed_mostly_english(self):
        assert (
            detect_language("Ti-6Al-4V tensile strength 1404 MPa elongation 11%")
            == "en"
        )

    def test_empty_text_defaults_english(self):
        assert detect_language("") == "en"
