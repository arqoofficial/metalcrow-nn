"""Configuration for the term-dictionary pipeline.

The entity-type label set is the team's canonical 8 (confirmed by AM,
2026-07-02). Keep it here so a rename is a one-line change and never
hard-coded into extraction logic.
"""

from __future__ import annotations

# Canonical entity-type labels emitted into spaCy EntityRuler patterns.
ENTITY_LABELS: tuple[str, ...] = (
    "MATERIAL",
    "PROCESS",
    "EQUIPMENT",
    "PROPERTY",
    "EXPERIMENT",
    "PUBLICATION",
    "EXPERT",
    "FACILITY",
)

# Fallback label for a term whose type is not yet known. The validation /
# curation step (or Vsevolod's schema) resolves UNKNOWN into a real label.
UNKNOWN_LABEL = "UNKNOWN"

# Cross-lingual sentence encoder. LaBSE places RU and EN synonyms in the same
# space by *meaning*, not co-occurrence — the fix for "no explicit link in
# text". Swap for "intfloat/multilingual-e5-base" to A/B the alternative.
DEFAULT_ENCODER = "sentence-transformers/LaBSE"

# Cosine-similarity threshold for two terms to share a synonym edge. Tuned
# conservatively; the LLM validation pass (candidate clusters only) catches
# false merges, so we bias toward recall here.
DEFAULT_SIM_THRESHOLD = 0.80

# Above this similarity a merge is auto-accepted; between the two thresholds a
# pair is flagged as "borderline" for cheap LLM / human review.
AUTO_ACCEPT_THRESHOLD = 0.90

__all__ = [
    "ENTITY_LABELS",
    "UNKNOWN_LABEL",
    "DEFAULT_ENCODER",
    "DEFAULT_SIM_THRESHOLD",
    "AUTO_ACCEPT_THRESHOLD",
]
