"""End-to-end term-dictionary pipeline.

Combines every term source and emits the two deliverables OSN asked for:

  (a) spaCy EntityRuler patterns   — term -> entity-type label
  (b) canonical-concept synonym map — RU / EN / acronym surface forms per concept

Term sources, all cheap / no-LLM:
  - seed glossary   (taxonomy + handbooks)          -> term, label
  - Schwartz-Hearst (acronym ↔ full-name pairs)      -> two linked surface forms
  - YAKE            (multi-word candidate terms)      -> term (label UNKNOWN)

Everything is unioned, then LaBSE clusters the surface forms cross-lingually so
«электроэкстракция», "electrowinning" and "EW" land in one concept. The output
carries provenance (which source proposed each term) and a per-cluster
confidence so the LLM/human validation step only inspects borderline merges.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import abbreviations as abbrev_mod
from . import schwartz_hearst, term_extract, wikidata
from .config import DEFAULT_ENCODER, DEFAULT_SIM_THRESHOLD, UNKNOWN_LABEL
from .seed import SeedTerm, load_seed_dir
from .synonym_cluster import SynonymCluster, SynonymClusterer

logger = logging.getLogger(__name__)


@dataclass
class TermRecord:
    """A surface form with its provisional label and provenance."""

    term: str
    label: str
    lang: str
    sources: set[str] = field(default_factory=set)


@dataclass
class PipelineResult:
    clusters: list[SynonymCluster]
    entity_patterns: list[dict]
    synonym_map: list[dict]
    stats: dict
    abbreviations: list[dict] = field(default_factory=list)


def _read_docs(doc_dir: str | Path) -> list[str]:
    doc_dir = Path(doc_dir)
    docs: list[str] = []
    if not doc_dir.exists():
        logger.warning("Doc dir not found: %s (building from seeds only)", doc_dir)
        return docs
    for path in sorted(doc_dir.iterdir()):
        if path.suffix.lower() in (".txt", ".md"):
            docs.append(path.read_text(encoding="utf-8"))
    logger.info("Read %d sample docs from %s", len(docs), doc_dir)
    return docs


def gather_terms(
    docs: list[str],
    seed_terms: list[SeedTerm],
) -> tuple[dict[str, TermRecord], list[tuple[str, str]]]:
    """Union every term source into one term -> TermRecord table.

    Returns the term table and the list of Schwartz-Hearst (short, long) pairs
    to feed the clusterer as must-link edges.
    """
    table: dict[str, TermRecord] = {}
    sh_pairs: list[tuple[str, str]] = []

    def add(term: str, label: str, lang: str, source: str) -> None:
        term = term.strip()
        if not term:
            return
        key = term.casefold()
        rec = table.get(key)
        if rec is None:
            table[key] = TermRecord(term=term, label=label, lang=lang,
                                    sources={source})
        else:
            rec.sources.add(source)
            if rec.label == UNKNOWN_LABEL and label != UNKNOWN_LABEL:
                rec.label = label

    for st in seed_terms:
        add(st.term, st.label, st.lang, "seed")

    for pair in schwartz_hearst.extract_pairs_from_docs(docs):
        # Acronym + expansion are two surface forms of one concept. Acronyms
        # embed unreliably (too short), so we record the pair as an explicit
        # must-link edge rather than hoping LaBSE re-discovers it.
        lang = term_extract.detect_lang(pair.long_form)
        add(pair.short_form, UNKNOWN_LABEL, lang, "schwartz_hearst")
        add(pair.long_form, UNKNOWN_LABEL, lang, "schwartz_hearst")
        sh_pairs.append((pair.short_form, pair.long_form))

    for cand in term_extract.extract_terms_from_docs(docs):
        add(cand.term, UNKNOWN_LABEL, cand.lang, "yake")

    logger.info("Gathered %d unique surface terms, %d Schwartz-Hearst pairs",
                len(table), len(sh_pairs))
    return table, sh_pairs


def build_entity_patterns(table: dict[str, TermRecord]) -> list[dict]:
    """spaCy EntityRuler patterns; labeled, junk-filtered, declension-deduped.

    Uses ``LOWER`` token patterns for multi-word terms (case-insensitive) and a
    plain string pattern for single tokens. UNKNOWN-label terms are held back
    until validation assigns a type — emitting them would mislabel entities.
    A precision filter (:mod:`term_dict.pattern_filter`) drops YAKE fragments,
    truncations, over-generic singletons, bare short tokens and declension
    duplicates; every drop is counted and logged (no silent truncation).
    """
    from collections import Counter

    from . import pattern_filter

    labeled = [(rec.term, rec.label, rec.sources)
               for rec in table.values() if rec.label != UNKNOWN_LABEL]

    reasons: Counter = Counter()
    survivors: list[tuple[str, str, set]] = []
    for term, label, sources in labeled:
        reason = pattern_filter.junk_reason(term, label, sources)
        if reason:
            reasons[reason] += 1
        else:
            survivors.append((term, label, sources))

    survivors, dropped = pattern_filter.dedup_declension(survivors)
    for _t, _l, reason in dropped:
        reasons[reason.split(":")[0]] += 1

    patterns: list[dict] = []
    for term, label, _sources in survivors:
        tokens = term.split()
        if len(tokens) == 1:
            patterns.append({"label": label, "pattern": term})
        else:
            patterns.append({
                "label": label,
                "pattern": [{"LOWER": tok.lower()} for tok in tokens],
            })

    n_pruned = len(labeled) - len(patterns)
    logger.info("Built %d EntityRuler patterns (pruned %d: %s)",
                len(patterns), n_pruned, dict(reasons))
    return patterns


def build_synonym_map(
    clusters: list[SynonymCluster],
    table: dict[str, TermRecord],
) -> list[dict]:
    """Canonical-concept synonym map with provenance + review flags."""
    from . import pattern_filter

    out: list[dict] = []
    for cl in clusters:
        # Drop brand-code / smart-quote junk surface forms, but never empty a
        # concept — keep all forms if filtering would remove every one.
        kept_terms = [t for t in cl.terms
                      if not pattern_filter.is_junk_surface_form(t)]
        use_terms = kept_terms or cl.terms
        members = []
        for term in use_terms:
            rec = table.get(term.casefold())
            members.append({
                "term": term,
                "label": rec.label if rec else cl.label,
                "lang": rec.lang if rec else None,
                "sources": sorted(rec.sources) if rec else [],
            })
        out.append({
            "concept_id": f"C{cl.cluster_id:05d}",
            "canonical": cl.canonical,
            "label": cl.label,
            "surface_forms": [m["term"] for m in members],
            "members": members,
            "min_edge_sim": cl.min_edge_sim,
            "needs_review": cl.borderline,
            # Realized same-concept links (term_a, term_b, confidence, method) —
            # the per-pair evidence behind this cluster; feeds entity_same_as.
            "same_as_edges": [
                {"a": a, "b": b, "confidence": w, "method": method}
                for a, b, w, method in cl.edges
            ],
        })
    return out


def run(
    doc_dir: str | Path = "data/sample_docs",
    seed_dir: str | Path = "data/seed",
    encoder: str = DEFAULT_ENCODER,
    sim_threshold: float = DEFAULT_SIM_THRESHOLD,
    do_cluster: bool = True,
    wikidata_must_link: str | Path = "data/wikidata_must_link.json",
    wikipedia_must_link: str | Path = "data/wikipedia_must_link.json",
    contract_must_link: str | Path = "data/contract_must_link.json",
) -> PipelineResult:
    """Run the full pipeline and return artifacts + stats.

    The interwiki glossaries (if harvested) are picked up two ways: their
    surface forms land in ``seed_dir`` as ``wikidata_glossary.jsonl`` /
    ``wikipedia_glossary.jsonl`` (loaded like any seed), and their ground-truth
    RU↔EN pairs are read from ``wikidata_must_link`` / ``wikipedia_must_link``
    and merged with Schwartz-Hearst pairs as must-link edges — so the clusterer
    never has to rediscover a pairing the interwiki sources already assert.
    """
    docs = _read_docs(doc_dir)
    seed_terms = load_seed_dir(seed_dir)
    table, sh_pairs = gather_terms(docs, seed_terms)

    # Ground-truth cross-lingual pairs from the interwiki harvests (optional).
    # Only keep pairs whose both endpoints actually appear in the term table,
    # so we never inject terms that no source proposed.
    present = set(table)

    def _present(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
        return [(a, b) for a, b in pairs
                if a.casefold() in present and b.casefold() in present]

    wd_pairs = _present(wikidata.load_must_link(wikidata_must_link))
    wp_pairs = _present(wikidata.load_must_link(wikipedia_must_link))
    # Contract-derived RU/EN alias groups (PROCESS_SEED/QUANTITY_KINDS + CQ
    # vocabulary): ground-truth same-concept pairs, bound to ontology/contracts.py.
    ct_pairs = _present(wikidata.load_must_link(contract_must_link))
    # Tag each must-link with its provenance method so the synonym map can emit
    # entity_same_as(confidence, method) truthfully per pair.
    must_link = (
        [(a, b, "schwartz_hearst") for a, b in sh_pairs]
        + [(a, b, "wikidata") for a, b in wd_pairs]
        + [(a, b, "wikipedia") for a, b in wp_pairs]
        + [(a, b, "contract") for a, b in ct_pairs]
    )

    if do_cluster:
        clusterer = SynonymClusterer(encoder_name=encoder, sim_threshold=sim_threshold)
        labels = {rec.term: rec.label for rec in table.values()}
        clusters = clusterer.cluster(
            [rec.term for rec in table.values()], labels, must_link=must_link)
        # Propagate a cluster's known label to its UNKNOWN members.
        for cl in clusters:
            if cl.label != UNKNOWN_LABEL:
                for term in cl.terms:
                    rec = table.get(term.casefold())
                    if rec and rec.label == UNKNOWN_LABEL:
                        rec.label = cl.label
    else:
        clusters = []

    entity_patterns = build_entity_patterns(table)
    synonym_map = build_synonym_map(clusters, table) if do_cluster else []
    abbreviations = [a.to_dict() for a in abbrev_mod.extract_abbreviations(docs)]

    stats = {
        "n_docs": len(docs),
        "n_seed_terms": len(seed_terms),
        "n_surface_terms": len(table),
        "n_labeled_terms": sum(r.label != UNKNOWN_LABEL for r in table.values()),
        "n_entity_patterns": len(entity_patterns),
        "n_concepts": len(clusters),
        "n_borderline": sum(c.borderline for c in clusters),
        "n_sh_pairs": len(sh_pairs),
        "n_abbreviations": len(abbreviations),
        "n_wikidata_pairs": len(wd_pairs),
        "n_wikipedia_pairs": len(wp_pairs),
        "n_contract_pairs": len(ct_pairs),
        "encoder": encoder if do_cluster else None,
        "sim_threshold": sim_threshold,
    }
    logger.info("Pipeline stats: %s", stats)
    return PipelineResult(clusters, entity_patterns, synonym_map, stats,
                          abbreviations=abbreviations)


def write_artifacts(result: PipelineResult, out_dir: str | Path = "out") -> None:
    """Persist EntityRuler patterns (JSONL) + synonym map + stats (JSON)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    patterns_path = out_dir / "entity_ruler_patterns.jsonl"
    with patterns_path.open("w", encoding="utf-8") as fh:
        for pat in result.entity_patterns:
            fh.write(json.dumps(pat, ensure_ascii=False) + "\n")

    (out_dir / "synonym_map.json").write_text(
        json.dumps(result.synonym_map, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out_dir / "abbreviations.json").write_text(
        json.dumps(result.abbreviations, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out_dir / "stats.json").write_text(
        json.dumps(result.stats, ensure_ascii=False, indent=2),
        encoding="utf-8")
    logger.info("Wrote artifacts to %s/", out_dir)


__all__ = ["run", "write_artifacts", "PipelineResult", "TermRecord", "gather_terms"]
