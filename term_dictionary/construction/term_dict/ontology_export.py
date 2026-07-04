"""Emit the glossary as ontology-contract-shaped artifacts.

We own the dictionary / EntityRuler / RU-EN alias-canonicalization stage; the
ontology (`ontology/contracts.py`, branch `onthology`) consumes it via well-known
shapes. We **bind by reference** — we match the contract's enum/registry names —
without importing the unmerged module. This adapter turns our build outputs
(`synonym_map.json`, `entity_ruler_patterns.jsonl`, `abbreviations.json`) into:

  * ``entity_aliases.seed.jsonl``   — {entity_type, entity_id, alias, source}
  * ``entity_same_as.seed.jsonl``   — {entity_type, source_alias, canonical_alias,
        confidence, method}  (per-pair, honest method — never star-with-fake-method)
  * ``process_alias_enrichment.json``   — additive RU/EN aliases for EXISTING
        ``ProcessType`` members (deduped against the contract snapshot)
  * ``quantity_kind_alias_enrichment.json`` — same for ``quantity_kinds``
  * ``proposed_new_process_types.json`` — corpus PROCESS terms that overflow the
        closed ``ProcessType`` enum, with evidence + frequency, for the onthology
        author + OSN to review (we do NOT add enum members ourselves)

The registry snapshot (``term_dict/data/ontology_registries_snapshot.json``,
produced by ``scripts/build_contract_seed.py``) is the dedup + resolution source.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

import click

logger = logging.getLogger(__name__)

# Our EntityRuler labels -> the contract's entity_type namespace.
LABEL_TO_ENTITY_TYPE = {
    "MATERIAL": "material",
    "PROCESS": "process",
    "PROPERTY": "quantity_kind",
    "EQUIPMENT": "equipment",
    "EXPERT": "person",
    "FACILITY": "lab",
    "PUBLICATION": "document",
    "EXPERIMENT": "experiment",
}

SNAPSHOT_PATH = "term_dict/data/ontology_registries_snapshot.json"

# Cross-lingual false friends that a semantic encoder confuses but which are NOT
# aliases of the resolved member (экстракция = extraction, not extrusion/экструзия).
_FALSE_FRIENDS = {
    ("extrusion", "экстракция"), ("extrusion", "extraction"),
}


def _load_snapshot(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        logger.warning("Registry snapshot not found: %s (run build_contract_seed)", p)
        return {"process_types": {}, "quantity_kinds": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def _alias_index(registry: dict) -> dict[str, str]:
    """alias(lower) -> canonical registry name, from a snapshot registry dict."""
    idx: dict[str, str] = {}
    for name, d in registry.items():
        for a in [name, name.replace("_", " "), *d.get("aliases", [])]:
            idx[a.strip().lower()] = name
    return idx


def _existing_aliases(registry: dict) -> dict[str, set[str]]:
    return {name: {a.lower() for a in [name, name.replace("_", " "), *d.get("aliases", [])]}
            for name, d in registry.items()}


def build_exports(synonym_map: list[dict], snapshot: dict) -> dict[str, object]:
    """Return every export artifact as in-memory Python objects."""
    proc_idx = _alias_index(snapshot.get("process_types", {}))
    qk_idx = _alias_index(snapshot.get("quantity_kinds", {}))
    proc_existing = _existing_aliases(snapshot.get("process_types", {}))
    qk_existing = _existing_aliases(snapshot.get("quantity_kinds", {}))

    entity_aliases: list[dict] = []
    entity_same_as: list[dict] = []
    proc_enrichment: dict[str, list[str]] = defaultdict(list)
    qk_enrichment: dict[str, list[str]] = defaultdict(list)
    proposed_processes: list[dict] = []

    for concept in synonym_map:
        label = concept["label"]
        etype = LABEL_TO_ENTITY_TYPE.get(label, "unknown")
        cid = concept["concept_id"]
        canonical = concept["canonical"]
        members = {m["term"]: m for m in concept["members"]}
        forms = list(members)

        # entity_aliases: one row per surface form.
        for term, m in members.items():
            entity_aliases.append({
                "entity_type": etype,
                "entity_id": cid,
                "alias": term,
                "source": "|".join(m.get("sources", [])) or "term_dict",
            })

        # entity_same_as: per non-canonical form, the strongest incident edge's
        # (confidence, method) toward the concept.
        best: dict[str, tuple[float, str]] = {}
        for e in concept.get("same_as_edges", []):
            for endpoint, other in ((e["a"], e["b"]), (e["b"], e["a"])):
                if endpoint == canonical:
                    continue
                cur = best.get(endpoint)
                if cur is None or e["confidence"] > cur[0]:
                    best[endpoint] = (e["confidence"], e["method"])
        for term, (conf, method) in best.items():
            entity_same_as.append({
                "entity_type": etype,
                "source_alias": term,
                "canonical_alias": canonical,
                "confidence": conf,
                "method": method,
            })

        # process / quantity resolution + enrichment vs the closed registries.
        if label == "PROCESS":
            resolved = _resolve_concept(forms, proc_idx)
            if resolved:
                _enrich(proc_enrichment, resolved, forms, proc_existing, proc_idx)
            else:
                proposed_processes.append({
                    "concept_id": cid,
                    "canonical": canonical,
                    "surface_forms": forms,
                    "frequency": len(forms),
                    "evidence_sources": sorted(
                        {s for m in members.values() for s in m.get("sources", [])}),
                    "note": "overflows closed ProcessType enum — review for new member",
                })
        elif label == "PROPERTY":
            resolved = _resolve_concept(forms, qk_idx)
            if resolved:
                _enrich(qk_enrichment, resolved, forms, qk_existing, qk_idx)

    return {
        "entity_aliases": entity_aliases,
        "entity_same_as": entity_same_as,
        "process_alias_enrichment": {k: sorted(set(v)) for k, v in proc_enrichment.items()},
        "quantity_kind_alias_enrichment": {k: sorted(set(v)) for k, v in qk_enrichment.items()},
        "proposed_new_process_types": _dedup_proposed(proposed_processes),
    }


def _enrich(enrichment: dict, resolved: str, forms: list[str],
            existing: dict[str, set[str]], idx: dict[str, str]) -> None:
    """Add only *new, unambiguous* aliases to an EXISTING registry member.

    Guards (from the critical review): never re-add an existing alias; never add
    an alias that resolves to a DIFFERENT member (quenching belongs to QUENCHING,
    not annealing); never add a known false friend (экстракция↛extrusion).
    """
    for f in forms:
        fl = f.lower()
        if fl in existing.get(resolved, set()):
            continue
        if fl in idx and idx[fl] != resolved:
            continue  # belongs to another registry member
        if (resolved, fl) in _FALSE_FRIENDS:
            continue
        enrichment[resolved].append(f)


def _dedup_proposed(proposed: list[dict]) -> list[dict]:
    """Collapse duplicate proposed-new entries by canonical (case-insensitive)."""
    seen: dict[str, dict] = {}
    for p in proposed:
        key = p["canonical"].lower()
        if key not in seen:
            seen[key] = p
        else:  # merge surface forms + keep max frequency
            merged = sorted(set(seen[key]["surface_forms"]) | set(p["surface_forms"]))
            seen[key]["surface_forms"] = merged
            seen[key]["frequency"] = max(seen[key]["frequency"], p["frequency"])
    return list(seen.values())


def _resolve_concept(forms: list[str], idx: dict[str, str]) -> str | None:
    """Resolve a concept's surface forms to a registry canonical (majority).

    Falls back to a per-token match for multiword forms so ``Froth flotation``
    resolves to the ``flotation`` member (→ alias enrichment) instead of being
    proposed as a spurious new member.
    """
    hits = [idx[f.lower()] for f in forms if f.lower() in idx]
    if not hits:
        for f in forms:
            for tok in f.lower().split():
                if tok in idx:
                    hits.append(idx[tok])
    if not hits:
        return None
    return max(set(hits), key=hits.count)


def write_exports(exports: dict, out_dir: str | Path) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("entity_aliases", "entity_same_as"):
        with (out / f"{name}.seed.jsonl").open("w", encoding="utf-8") as fh:
            for row in exports[name]:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    for name in ("process_alias_enrichment", "quantity_kind_alias_enrichment",
                 "proposed_new_process_types"):
        (out / f"{name}.json").write_text(
            json.dumps(exports[name], ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "Exports: %d entity_aliases, %d entity_same_as, %d process-enrich, "
        "%d quantity-enrich, %d proposed-process",
        len(exports["entity_aliases"]), len(exports["entity_same_as"]),
        len(exports["process_alias_enrichment"]),
        len(exports["quantity_kind_alias_enrichment"]),
        len(exports["proposed_new_process_types"]))


@click.command()
@click.option("--synonym-map", default="out_corpus/synonym_map.json", show_default=True)
@click.option("--snapshot", default=SNAPSHOT_PATH, show_default=True)
@click.option("--out-dir", default="out_corpus/ontology", show_default=True)
def main(synonym_map: str, snapshot: str, out_dir: str) -> None:
    """Emit contract-shaped export artifacts from a built synonym map."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sm = json.loads(Path(synonym_map).read_text(encoding="utf-8"))
    exports = build_exports(sm, _load_snapshot(snapshot))
    write_exports(exports, out_dir)
    counts = {k: len(v) for k, v in exports.items()}
    click.echo(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
