# Term Dictionary — RU/EN domain gazetteer, synonym map & jargon layer

The **dictionary / EntityRuler / RU-EN alias-canonicalization stage** of the
Научный клубок KG. It is the cheap second extraction pass the ontology names in
`ONTOLOGY_V2.md` §5.1 step 7 — *"spaCy EntityRuler вторым дешёвым проходом
(словарь авто-собран из выхода NuExtract)"* (`ExtractorKind.SPACY`) — plus the
cross-lingual synonym map and the jargon/abbreviation layer that feed
`entity_aliases`, `entity_same_as(confidence, method)`, and the open
`process_types` / `quantity_kinds` registries.

It **binds to the contract by reference**: labels and registry names match
`ontology/contracts.py` (branch `onthology`); nothing here imports that unmerged
module. A later integration step wires these artifacts into `workers/etl`.

## Consume it (zero heavy deps)

```python
from term_dictionary.ontology_loader import TermDictionary
td = TermDictionary("term_dictionary/data")

td.entity_ruler_patterns()          # [{label, pattern}] → spaCy EntityRuler (ExtractorKind.SPACY)
td.entity_aliases()                 # [{entity_type, entity_id, alias, source}]
td.entity_same_as()                 # [{entity_type, source_alias, canonical_alias, confidence, method}]
td.process_alias_enrichment()       # {ProcessType_value: [new RU/EN aliases]}   (additive)
td.quantity_kind_alias_enrichment() # {quantity_kind: [new RU/EN aliases]}       (additive)
td.proposed_new_process_types()     # [{canonical, surface_forms, frequency, evidence_sources}]
td.abbreviations()                  # [{acronym, canonical_expansion, expansion_variants, confidence, n_docs}]
td.abbreviation_lookup()            # {acronym|variant → canonical_expansion}
```

The loader is pure stdlib (json only) — no LaBSE/numpy/network. The heavy
construction tooling stays under `construction/` and is not needed to consume.

## What's in `data/`

| File | Shape | Feeds |
|------|-------|-------|
| `entity_ruler_patterns.jsonl` | `{label, pattern}` (924) | spaCy EntityRuler pass |
| `synonym_map.json` | 503 concepts, each with `surface_forms`, `members`, per-pair `same_as_edges` | canonicalization |
| `abbreviations.json` | 60 acronyms, declension-consolidated + confidence | jargon resolution |
| `ontology/entity_aliases.seed.jsonl` | `{entity_type, entity_id, alias, source}` (1289) | `experiments.entity_aliases` |
| `ontology/entity_same_as.seed.jsonl` | `{entity_type, source_alias, canonical_alias, confidence, method}` (788) | `experiments.entity_same_as` |
| `ontology/process_alias_enrichment.json` | `{ProcessType: [new aliases]}` | `process_types.aliases[]` (additive) |
| `ontology/quantity_kind_alias_enrichment.json` | `{quantity_kind: [new aliases]}` | `quantity_kinds.aliases[]` (additive) |
| `ontology/proposed_new_process_types.json` | overflow terms + evidence + frequency | **human review** (see below) |
| `stats.json` | build metrics | — |

`entity_type` values match the contract namespace: `material, process,
quantity_kind, equipment, lab, document, person, experiment`.

### `proposed_new_process_types.json` needs a human decision

`ProcessType` is a **closed enum** we don't own, so we never add members. Corpus
process terms that overflow the enum (comminution/измельчение, crushing/
дробление, filtration, sintering, beneficiation, magnetic separation,
pelletizing, electrometallurgy, …) are emitted here with evidence + frequency
for the `onthology` author + OSN to accept or reject. `*_alias_enrichment.json`
only ever *adds aliases to existing* members, deduped against the contract.

## How it's built (source terms, all cheap / no per-token LLM)

Seed glossary (incl. **contracts.py `PROCESS_SEED` + `QUANTITY_KINDS_SEED` folded
as must-keep seed**) + Wikidata/Wikipedia interwiki harvest + Schwartz-Hearst
acronyms + YAKE → **LaBSE** cross-lingual clustering (must-link-first +
**complete-linkage** + ground-group guard → no single-linkage blobs) →
EntityRuler patterns + synonym map + `entity_same_as(confidence, method)`.

Encoder note (per SPEC_V3): **LaBSE is used strictly offline** for
dictionary-construction (bitext alignment of RU↔EN terms by meaning). The
runtime text-embedding path stays `multilingual-e5-large` per SPEC_V3 — nothing
emitted here feeds the runtime encoder.

Rebuild: see `construction/README.md` (`uv run python cli.py build …`).

## Known limitations (honest)

- **Built from a bounded 15-doc `Обзоры` sample**, not the full 1453-doc corpus.
  Coverage is strong on materials/processes/properties and the target
  competency-question vocabulary (water desalination/TDS, catholyte, injection,
  ТЭП/CAPEX/OPEX, PGM/matte/slag — all present via the contract-seed fold), but
  the long tail wants a CQ-stratified corpus slice (queued).
- **`EXPERT / FACILITY / PUBLICATION / EXPERIMENT` labels are empty** — those
  entities come from the staff/lab registry and document metadata, not review
  prose; deferred until that registry lands (the ontology's `TeamLab`/`Document`
  are populated by structured ETL, not this gazetteer).
- Enrichment is **guarded**: an alias that resolves to a *different* registry
  member (`annealing←quenching`) or is a cross-lingual false friend
  (`extrusion←экстракция`) is never added; any cluster spanning >1 known label is
  flagged `needs_review`. Two ≤2-char contract aliases that collide with element
  symbols (`elongation:"At"`, `yield_strength:"YS"`) were dropped from our seed and
  reported in `construction/data/contract_alias_collisions.json` for the
  `onthology` author (we never edit the contract).
- Domain-BERT question (OSN): **no** English-only materials BERT — the corpus is
  RU-primary; domain lives in this gazetteer/must-link layer. If the clustering
  encoder is ever upgraded, `BAAI/bge-m3` (MIT) is the drop-in; keep LaBSE
  otherwise. NER fine-tuning (if pursued) → `ai-forever/ruSciBERT`.
