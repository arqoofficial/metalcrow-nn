# Glossary v3 — NuExtract3 corpus harvest

Built 2026-07-04 from the full cleaned corpus (562 Docling docs, Yandex share) via a
4h / 4×3090 NuExtract3 harvest over a max-coverage sample, then distilled into a typed
gazetteer. This is the build that **breaks the typing bottleneck**: v2 could only type a
term by clustering it onto one of 949 seeds; NuExtract types from context, so ~32k
previously-untypable terms got real labels.

## Contents
| file | what |
|---|---|
| `entity_ruler_patterns.jsonl` | 28,176 spaCy EntityRuler patterns, 5 types |
| `synonym_map.json` | 18,409 concepts (6,092 multi-term; 408 cross-lingual RU↔EN) |
| `abbreviations.json` | 8,264 acronym→expansion (NuExtract-SH-validated ∪ v2) |
| `term_table.json` | the raw voted term→label table (provenance) |
| `stats.json` | build stats |

## v3 vs v2
| metric | v2 | v3 | ×) |
|---|---|---|---|
| EntityRuler patterns | 1,305 | **28,176** | 21.6× |
| synonym concepts | 5,639 | **18,409** | 3.3× |
| abbreviations | 5,379 | **8,264** | 1.5× |
| patterns by type | mostly MATERIAL | MATERIAL 14,148 / PROPERTY 5,439 / PROCESS 4,245 / EQUIPMENT 4,111 / FACILITY 233 | — |

The EQUIPMENT lexicon (v2 benchmark F1 0.14 — near-empty) now has 4,111 patterns read
from context; FACILITY (33 seeds → 233) is a new type recovered from EQUIPMENT
mis-labels (NuExtract's 4-type template had no FACILITY slot).

## Method
1. **Sample** — 234M-char corpus → 44,624 paragraph passages (~1800 tok), ranked by
   greedy weighted max-coverage of the DF≥2 vocabulary. Top-24,465 harvested (4h) =
   ~99.7% vocab coverage.
2. **Harvest** — NuExtract3 (4B, template + 3 ICL), 24,465 passages, 6.1% `_raw`
   partials salvaged.
3. **Vote** — 815k mentions → per-term type-agreement voting (≥3 mentions, ≥2 docs,
   ≥0.6 agreement) → 32,206 confident typed terms. Turns ~0.32 per-mention precision
   into high per-term precision.
4. **Assemble** — inject seeds; memory-safe e5 clustering (per-type blockwise +
   union-find + blob guard) → synonym map; pattern_filter → EntityRuler.
5. **Curate (v3.1)** — drop 411 author-initial fragments; relabel 233 org/plant →
   FACILITY.

## Known limits
- **Precision is high-recall-biased.** NuExtract over-extracts; voting + pattern_filter
  clean most, but ~single English tokens (2,339) and some economic/generic terms remain
  un-reviewed. Right as a cheap first-pass tagger; human/LLM review recommended before
  authoritative use.
- Silver, not human-gold. Threshold-tuning (MIN_MENTIONS / AGREE) is a lever if higher
  precision is wanted at some recall cost.

Repro: `v3build/` — `corpus_lib.py`, `chunker.py`, `sampler.py`, `build_v3.py`,
`memsafe_cluster.py`, `clean_v3.py`.
