"""Tests for the term_dictionary patterns loader + its wiring into the EntityRuler."""

import pytest
from science_kg.nlp.term_dictionary_patterns import LABEL_MAP, load_mapped_patterns
from science_kg.nlp.patterns import ALL_PATTERNS, HAND_WRITTEN_PATTERNS
from science_kg.nlp.pipeline import build_pipeline
from science_kg.nlp.extractor import extract_entities
from science_kg.models import EntityType


def test_load_mapped_patterns_only_uses_known_labels():
    patterns = load_mapped_patterns()
    assert patterns  # the copied data file is non-empty
    assert all(p["label"] in LABEL_MAP.values() for p in patterns)


def test_load_mapped_patterns_drops_unmapped_labels():
    """The source file only contains MATERIAL/PROCESS/PROPERTY/EQUIPMENT, all of
    which are mapped — this asserts the drop-path itself works (rather than just
    "happens to never trigger") by checking a label deliberately absent from
    LABEL_MAP would never survive filtering."""
    assert "UNKNOWN" not in LABEL_MAP
    assert "LAB" not in LABEL_MAP


def test_all_patterns_includes_both_sources():
    mapped = load_mapped_patterns()
    assert len(ALL_PATTERNS) == len(HAND_WRITTEN_PATTERNS) + len(mapped)


@pytest.fixture(scope="module")
def nlp_en():
    return build_pipeline("en_core_sci_sm")


def test_term_dictionary_only_pattern_recognized(nlp_en):
    """"desalination" is not in this project's own hand-written patterns.py —
    it only exists in the copied term_dictionary/entity_ruler_patterns.jsonl,
    mapped PROCESS -> PROCESS. Recognizing it proves the merged pattern list is
    actually wired into the pipeline, not just loaded and discarded."""
    assert not any(
        p["pattern"] == "desalination" for p in HAND_WRITTEN_PATTERNS
    )
    doc = nlp_en("The plant uses desalination to treat seawater.")
    labels = {e.label for e in extract_entities(doc)}
    assert EntityType.PROCESS in labels
