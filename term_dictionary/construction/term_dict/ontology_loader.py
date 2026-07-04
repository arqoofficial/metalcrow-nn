"""Zero-dependency loader for the term-dictionary deliverable.

This is the clean adapter the ontology/ETL integration consumes. It reads only
JSON/JSONL from a deliverable directory (no LaBSE, no numpy, no network — the
heavy construction tooling stays on our side), and returns plain Python shaped
to the ontology contract (`ontology/contracts.py`):

    from term_dictionary.ontology_loader import TermDictionary
    td = TermDictionary("data")                 # deliverable dir
    td.entity_ruler_patterns()                  # [{label, pattern}, ...] (ExtractorKind.SPACY)
    td.entity_aliases()                         # [{entity_type, entity_id, alias, source}]
    td.entity_same_as()                         # [{entity_type, source_alias, canonical_alias, confidence, method}]
    td.process_alias_enrichment()               # {ProcessType_value: [new aliases]}
    td.quantity_kind_alias_enrichment()         # {quantity_kind: [new aliases]}
    td.proposed_new_process_types()             # [{canonical, surface_forms, frequency, ...}]
    td.abbreviations()                          # [{acronym, canonical_expansion, variants, confidence, ...}]

Every accessor tolerates a missing file (returns an empty list/dict + a log
line) so partial deliverables never hard-fail the consumer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class TermDictionary:
    """Read-only view over a term-dictionary deliverable directory."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base = Path(base_dir)

    # --- internals -------------------------------------------------------
    def _json(self, name: str, default):
        p = self.base / name
        if not p.exists():
            logger.warning("term-dictionary: missing %s", p)
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    def _jsonl(self, name: str) -> list[dict]:
        p = self.base / name
        if not p.exists():
            logger.warning("term-dictionary: missing %s", p)
            return []
        return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]

    # --- public accessors ------------------------------------------------
    def entity_ruler_patterns(self) -> list[dict]:
        """spaCy EntityRuler patterns — the cheap second ER pass (ExtractorKind.SPACY)."""
        return self._jsonl("entity_ruler_patterns.jsonl")

    def synonym_map(self) -> list[dict]:
        return self._json("synonym_map.json", [])

    def abbreviations(self) -> list[dict]:
        return self._json("abbreviations.json", [])

    def entity_aliases(self) -> list[dict]:
        return self._jsonl("ontology/entity_aliases.seed.jsonl")

    def entity_same_as(self) -> list[dict]:
        return self._jsonl("ontology/entity_same_as.seed.jsonl")

    def process_alias_enrichment(self) -> dict:
        return self._json("ontology/process_alias_enrichment.json", {})

    def quantity_kind_alias_enrichment(self) -> dict:
        return self._json("ontology/quantity_kind_alias_enrichment.json", {})

    def proposed_new_process_types(self) -> list[dict]:
        return self._json("ontology/proposed_new_process_types.json", [])

    def abbreviation_lookup(self) -> dict[str, str]:
        """acronym / every attested variant -> canonical expansion (fast resolve)."""
        out: dict[str, str] = {}
        for ab in self.abbreviations():
            canon = ab["canonical_expansion"]
            out[ab["acronym"].lower()] = canon
            for v in ab.get("expansion_variants", []):
                out[v.lower()] = canon
        return out


__all__ = ["TermDictionary"]
