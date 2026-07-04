# Vendored ontology contract reference (read-only)

Source: `arqoofficial/metalcrow` branch `onthology`, commit `2bd21160163a48db0222fe67a10dff6dbcb474c2`.
Files: `contracts_onthology.py` (pydantic contract), `ONTOLOGY_V2.md` (spec).

We **bind by reference** to this contract (match its enum/registry names) but do
NOT import the unmerged module (per integration decision). Refresh these copies +
re-run `scripts/build_contract_seed.py` when the onthology contract changes.
