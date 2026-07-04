"""CLI for the Nornickel term-dictionary pipeline."""

from __future__ import annotations

import json
import logging

import click

from term_dict import pipeline, schwartz_hearst, wikidata, wikipedia
from term_dict.config import DEFAULT_ENCODER, DEFAULT_SIM_THRESHOLD


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Debug logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Build a RU/EN spaCy gazetteer + cross-lingual synonym map."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)


@cli.command()
@click.argument("path", type=click.Path(exists=True))
def acronyms(path: str) -> None:
    """Extract Schwartz-Hearst acronym pairs from a text file (no model load)."""
    text = open(path, encoding="utf-8").read()
    pairs = schwartz_hearst.extract_pairs(text)
    for p in pairs:
        click.echo(f"{p.short_form}\t{p.long_form}")
    click.echo(f"# {len(pairs)} pairs", err=True)


@cli.command()
@click.option("--anchors", default="data/wikidata_anchors.tsv", show_default=True)
@click.option("--no-elements", is_flag=True, help="Skip the P31 element sweep.")
@click.option("--wiki-terms", default="data/wikipedia_terms.tsv", show_default=True)
@click.option("--no-wikipedia", is_flag=True,
              help="Skip the Wikipedia langlinks harvest.")
def harvest(anchors, no_elements, wiki_terms, no_wikipedia) -> None:
    """Harvest RU↔EN pairs from Wikidata + Wikipedia → glossary + must-link.

    Corpus-independent, cached under data/wikidata_cache/. Wikidata writes
    data/seed/wikidata_glossary.jsonl + data/wikidata_must_link.json; the
    Wikipedia langlinks harvest writes the wikipedia_* counterparts (fills the
    specialized unit-op/equipment long tail Wikidata lacks a RU label for).
    Both glossaries load as seeds; both pair files feed the clusterer.
    """
    h = wikidata.WikidataHarvester()
    concepts = h.build_concepts(anchors, include_elements=not no_elements)
    wikidata.write_glossary(concepts)
    n_forms = sum(len(c.surface_forms()) for c in concepts)
    n_pairs = sum(len(c.must_link_pairs()) for c in concepts)
    click.echo(f"Wikidata: {len(concepts)} concepts, {n_forms} surface forms, "
               f"{n_pairs} must-link pairs")

    if not no_wikipedia:
        # Reuse the same polite cached HTTP client / on-disk cache.
        wp = wikipedia.harvest(wiki_terms, http=h)
        wp_pairs = sum(len(c.must_link_pairs()) for c in wp)
        click.echo(f"Wikipedia: {len(wp)} concepts, {wp_pairs} must-link pairs")


@cli.command()
@click.option("--doc-dir", default="data/sample_docs", show_default=True)
@click.option("--seed-dir", default="data/seed", show_default=True)
@click.option("--out-dir", default="out", show_default=True)
@click.option("--encoder", default=DEFAULT_ENCODER, show_default=True)
@click.option("--sim-threshold", default=DEFAULT_SIM_THRESHOLD, show_default=True, type=float)
@click.option("--no-cluster", is_flag=True, help="Skip LaBSE clustering (fast).")
def build(doc_dir, seed_dir, out_dir, encoder, sim_threshold, no_cluster) -> None:
    """Run the full pipeline and write artifacts to OUT_DIR."""
    result = pipeline.run(
        doc_dir=doc_dir, seed_dir=seed_dir, encoder=encoder,
        sim_threshold=sim_threshold, do_cluster=not no_cluster,
    )
    pipeline.write_artifacts(result, out_dir)
    click.echo(json.dumps(result.stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli()
