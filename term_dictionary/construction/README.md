# Construction tooling (rebuild the term dictionary)

Heavy-dependency pipeline that *produces* `../data/`. Not needed to **consume**
the deliverable (use `../ontology_loader.py`, pure stdlib). Kept here for
reproducibility.

## Rebuild

```bash
cd term_dictionary/build
uv sync
# 1. Refresh the free interwiki glossaries (cached under data/wikidata_cache/)
uv run python cli.py harvest
# 2. (Re)derive the contract seed + registry snapshot from the vendored contract
uv run python scripts/build_contract_seed.py --contracts data/reference/contracts_onthology.py
# 3. Build gazetteer + synonym map + abbreviations (loads LaBSE on CPU)
uv run python cli.py build --doc-dir data/corpus_sample --seed-dir data/seed --out-dir out_corpus
# 4. Emit the contract-shaped ontology artifacts
uv run python -m term_dict.ontology_export --synonym-map out_corpus/synonym_map.json --out-dir out_corpus/ontology
# 5. Copy out_corpus/* into ../data/ to update the deliverable
uv run pytest        # 56 tests, offline (stub encoder; no LaBSE download)
```

## Layout

- `term_dict/` — pipeline: `seed`, `wikidata`, `wikipedia`, `schwartz_hearst`,
  `abbreviations`, `term_extract`, `synonym_cluster` (complete-linkage +
  ground-group guard), `pattern_filter`, `ontology_export`, `ontology_loader`,
  `pipeline`, `config`.
- `scripts/build_contract_seed.py` — extracts `PROCESS_SEED`/`QUANTITY_KINDS_SEED`
  from the vendored `data/reference/contracts_onthology.py` (bind-by-reference).
- `scripts/parse_reviews_sample.py` — reproduces the 15-doc born-digital sample.
- `data/reference/` — vendored ontology contract + spec (the bind target).
- `data/corpus_sample/` — the 15 parsed review docs the build reads.

The vendored contract is `onthology@2bd2116`. If it changes upstream, refresh
`data/reference/` and re-run step 2.
