# Glossary v3 — E2 extraction benchmark (v2 → v3 delta)

The v3 glossary was dropped into the **E2 head** (spaCy `ru_core_news_lg` +
EntityRuler, lemma+lower patterns — the SPEC-prescribed gazetteer head) and scored
against the **same** silver gold, slice, and matching mode as the v2 run. Nothing
in the harness changed — only the gazetteer. So this delta isolates the glossary.

- **Head:** `benchmark/candidates/e2_spacy.py` (unchanged)
- **Slice:** `slice/slice_v1.json` (68 passages) · **Gold:** `slice/silver_gold.json`
  (30 model-assisted SILVER docs, 281 entities: 178 MATERIAL / 55 PROCESS /
  29 PROPERTY / 19 EQUIPMENT)
- **Matching:** `relaxed` (same entity type + head-overlap after RU stemming) — the
  benchmark default
- v2 gazetteer = 1,305 patterns → 4,102 compiled ruler patterns;
  v3 gazetteer = 28,176 patterns → **43,628** compiled ruler patterns

## Headline

| metric | E2 (v2) | E2 (v3) | Δ |
|---|---|---|---|
| **NER micro F1** | 0.307 | **0.352** | +15% |
| NER macro F1 | 0.286 | **0.360** | +26% |
| micro precision | 0.262 | 0.220 | −16% |
| **micro recall** | 0.370 | **0.879** | **+137%** |
| tp / fp / fn | 104 / 293 / 177 | 247 / 875 / 34 | recall near-saturated |

## Per-type NER F1 (the story is EQUIPMENT)

| type | E2 v2 F1 | E2 v3 F1 | v2 recall → v3 recall |
|---|---|---|---|
| MATERIAL | 0.300 | **0.381** | 0.421 → 0.910 |
| PROCESS | 0.434 | 0.313 | 0.327 → 0.764 |
| PROPERTY | 0.273 | 0.240 | 0.310 → 0.897 |
| **EQUIPMENT** | **0.138** | **0.508** | 0.105 → **0.895** |

**EQUIPMENT F1 0.14 → 0.51 (3.7×).** The v2 gazetteer had almost no equipment
lexicon; the NuExtract3 corpus harvest read equipment terms from context and filled
it. At 0.51 the free CPU gazetteer now **matches NuExtract-3shot's own EQUIPMENT F1
(0.51)** on this slice — the extractor's equipment competence has been distilled
into a zero-cost lookup.

## Honest reading (recall↔precision knob)

- The lift is **recall-driven** (0.37 → 0.88); micro F1 rises because recall
  swamps a modest precision dip (0.262 → 0.220). This is exactly the **high-recall
  bias** flagged in the glossary README: v3 over-tags.
- **PROCESS regressed** (F1 0.43 → 0.31): precision fell 0.64 → 0.20 as generic
  process-ish spans got tagged, though recall more than doubled (0.33 → 0.76).
- Gold is **non-exhaustive silver**, so reported precision is a *lower bound*
  (some "false positives" are real mentions the gold skipped). Recall and the
  relative v2→v3 ranking are the trustworthy signals.
- **Precision levers:** raise `MIN_MENTIONS` (currently 3) and/or `AGREEMENT`
  (currently 0.60) at build time to trade recall for precision. This benchmark is
  the loosest (highest-recall) operating point.

Reproduce:
```
python candidates/e2_spacy.py --gazetteer data/glossary_v3/entity_ruler_patterns.jsonl \
    --slice slice/slice_v1.json --out predictions/E2_v3.json
python tools/score_one.py --gold slice/silver_gold.json --pred predictions/E2_v3.json \
    --name E2_v3 --ner-mode relaxed --out scorecards/E2_v3.json
```
