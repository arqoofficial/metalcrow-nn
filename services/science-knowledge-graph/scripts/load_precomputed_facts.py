"""Bulk-load spaCy-extracted facts + precomputed OpenAI vectors into Neo4j.

Reads ``SHARED/facts/*.json`` and the matching vectors from
``SHARED/vectors/`` and writes entities/relations/documents directly into
Neo4j using ``science_kg.graph.neo4j_client``. No spaCy re-run, no embedding
API calls — everything is taken from the offline artifacts built by
``scripts/embed_facts.py`` and ``scripts/build_facts_db.py``.

Implementation uses a two-pass strategy to keep Neo4j fast:
  1. Create the graph structure (entities, relations, documents) without
     embeddings. This avoids expensive vector-index updates inside large MERGE
     transactions.
  2. Backfill embeddings in batches keyed by (text, type).

Optionally reads the original ``.md`` files (by doc_id stem) so that
``Document.text`` is available for RAG source resolution.

Usage:

    PYTHONPATH=. .venv/bin/python scripts/load_precomputed_facts.py \
        ../nornickel-2026-parser/SHARED/facts/facts \
        ../nornickel-2026-parser/SHARED/vectors \
        --md-dir ../nornickel-2026-parser/SHARED/RAW_DATA_646/RAW_DATA \
        --batch-size 50

Run inside the science-knowledge-graph container/service so Neo4j is
reachable (default ``bolt://localhost:7687``).
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from science_kg.config import settings
from science_kg.graph.neo4j_client import Neo4jClient
from science_kg.models import Entity, EntityType, Relation, RelationType
from science_kg.nlp.normalizer import canonical_material, canonical_process

app = typer.Typer(help="Load precomputed facts + vectors into Neo4j.")
console = Console()

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)

_LEGACY_LABEL_MAP: dict[str, str] = {
    "REGIME": "PROCESS",
    "VALUE": "PROPERTY",
}

_LEGACY_RELATION_MAP: dict[str, str] = {
    "PROCESSED_BY": "uses_material",
    "AFFECTS": "produces_output",
    "MEASURED_BY": "validated_by",
}


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)


def _normalize_label(label: str) -> str | None:
    label = _LEGACY_LABEL_MAP.get(label, label)
    try:
        EntityType(label)
    except ValueError:
        return None
    return label


def _canonical_entity(text: str, label: str) -> str:
    if label == "MATERIAL":
        return canonical_material(text)
    if label in ("PROCESS", "REGIME"):
        return canonical_process(text)
    return text.strip().lower()


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_entity_vectors(vectors_dir: Path) -> dict[tuple[str, str], list[float]]:
    """Return lookup (canonical_text, label) -> vector."""
    rows = _load_jsonl(vectors_dir / "entities.jsonl")
    matrix = np.load(vectors_dir / "entities.npy")
    if len(rows) != matrix.shape[0]:
        raise ValueError("entities.npy / entities.jsonl shape mismatch")
    return {
        (row["text"], row["label"]): matrix[i].astype(np.float64).tolist()
        for i, row in enumerate(rows)
    }


def _build_md_index(md_dir: Path) -> dict[str, Path]:
    """Build a stem -> path index for fast lookup. Stems are NFC-normalized."""
    return {
        unicodedata.normalize("NFC", path.stem): path
        for path in md_dir.rglob("*.md")
    }


def _read_md_body(md_path: Path | None) -> str:
    if md_path is None:
        return ""
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    return _strip_frontmatter(text)


def _vector_for_entity(
    text: str, label: str, entity_vectors: dict[tuple[str, str], list[float]]
) -> list[float] | None:
    canonical = _canonical_entity(text, label)
    return entity_vectors.get((canonical, label))


async def _doc_exists(client: Neo4jClient, doc_id: str) -> bool:
    return await client.get_document(doc_id) is not None


@app.command()
def main(
    facts_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Папка с facts JSON.",
    ),
    vectors_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Папка с векторами (entities.npy/jsonl).",
    ),
    md_dir: Path | None = typer.Option(
        None,
        "--md-dir",
        help="Папка с исходными .md для загрузки Document.text (опционально).",
    ),
    neo4j_uri: str = typer.Option(settings.neo4j_uri, "--neo4j-uri"),
    neo4j_user: str = typer.Option(settings.neo4j_user, "--neo4j-user"),
    neo4j_password: str = typer.Option(settings.neo4j_password, "--neo4j-password"),
    batch_size: int = typer.Option(
        50,
        "--batch-size",
        help="Количество документов в одной транзакции Neo4j (структура).",
    ),
    embedding_batch: int = typer.Option(
        500,
        "--embedding-batch",
        help="Количество сущностей в одном batch backfill'а эмбеддингов.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Загрузить только N первых документов (smoke-test).",
    ),
    skip_existing: bool = typer.Option(
        False,
        "--skip-existing",
        help="Пропускать документы, уже присутствующие в Neo4j.",
    ),
) -> None:
    """Load precomputed facts + vectors into Neo4j."""
    entity_vectors = _load_entity_vectors(vectors_dir)
    console.print(
        f"[bold]Загружено векторов сущностей:[/bold] [cyan]{len(entity_vectors)}[/cyan]"
    )

    md_index = _build_md_index(md_dir) if md_dir else {}
    if md_dir:
        console.print(f"[bold]Найдено .md:[/bold] [cyan]{len(md_index)}[/cyan]")

    fact_files = sorted(facts_dir.rglob("*.json"))
    if limit is not None:
        fact_files = fact_files[:limit]

    if not fact_files:
        console.print("[yellow]Нет facts-файлов[/yellow]")
        raise typer.Exit(0)

    client = Neo4jClient(neo4j_uri, neo4j_user, neo4j_password)

    async def _load() -> None:
        await client.bootstrap_schema()

        # (surface text, label) -> vector, collected during pass 1.
        embeddings_to_set: dict[tuple[str, str], list[float]] = {}
        total_entities = 0
        total_relations = 0
        skipped = 0

        # ── Pass 1: graph structure (no embeddings) ───────────────────────────
        console.print("\n[bold]Pass 1/2:[/bold] создание структуры графа…")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Документы", total=len(fact_files))

            for i in range(0, len(fact_files), batch_size):
                batch_files = fact_files[i : i + batch_size]
                batch_entities: list[Entity] = []
                batch_relations: list[Relation] = []
                docs_to_upsert: list[tuple[str, str, dict]] = []

                for path in batch_files:
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError as exc:
                        console.print(f"[red]skip malformed {path}: {exc}[/red]")
                        progress.advance(task)
                        continue

                    doc_id = data.get("doc_id") or path.stem

                    if skip_existing and await _doc_exists(client, doc_id):
                        skipped += 1
                        progress.advance(task)
                        continue

                    entities = [
                        Entity(
                            text=e.get("text", "").strip(),
                            label=EntityType(label),
                            start_char=e.get("start_char", 0),
                            end_char=e.get("end_char", 0),
                            source_doc=doc_id,
                        )
                        for e in data.get("entities", [])
                        if (label := _normalize_label(e.get("label", "")))
                        and len(e.get("text", "").strip()) >= 2
                    ]

                    relations: list[Relation] = []
                    for r in data.get("relations", []):
                        rel_type = _LEGACY_RELATION_MAP.get(
                            r.get("relation", ""), r.get("relation", "")
                        )
                        try:
                            relation = RelationType(rel_type)
                        except ValueError:
                            continue
                        src_type = _normalize_label(r.get("source_type", ""))
                        tgt_type = _normalize_label(r.get("target_type", ""))
                        if not src_type or not tgt_type:
                            continue
                        relations.append(
                            Relation(
                                source=r.get("source", "").strip(),
                                source_type=EntityType(src_type),
                                relation=relation,
                                target=r.get("target", "").strip(),
                                target_type=EntityType(tgt_type),
                                verb=r.get("verb", ""),
                                source_doc=doc_id,
                            )
                        )

                    # Collect embeddings to backfill later.
                    for ent in entities:
                        vec = _vector_for_entity(ent.text, ent.label.value, entity_vectors)
                        if vec is not None:
                            embeddings_to_set[(ent.text, ent.label.value)] = vec

                    batch_entities.extend(entities)
                    batch_relations.extend(relations)

                    if md_index:
                        md_path = md_index.get(unicodedata.normalize("NFC", doc_id))
                        body = _read_md_body(md_path)
                        meta = data.get("metadata", {})
                        docs_to_upsert.append(
                            (
                                doc_id,
                                body,
                                {
                                    "source_file": data.get("source_file", path.name),
                                    "language": meta.get("language", "unknown"),
                                    "char_count": meta.get("char_count", 0),
                                    "n_chunks": meta.get("n_chunks", 0),
                                    "parsed_at": meta.get("parsed_at", ""),
                                    "loaded_at": datetime.now(tz=UTC).isoformat(),
                                },
                            )
                        )

                    progress.advance(task)

                if batch_entities:
                    await client.upsert_entities(batch_entities)
                    total_entities += len(batch_entities)
                if batch_relations:
                    await client.upsert_relations(batch_relations)
                    total_relations += len(batch_relations)
                for doc_id, body, meta in docs_to_upsert:
                    await client.upsert_document(doc_id, body, meta)

        # ── Pass 2: backfill embeddings ───────────────────────────────────────
        console.print("\n[bold]Pass 2/2:[/bold] backfill эмбеддингов…")
        embedding_items = list(embeddings_to_set.items())
        # Flatten to (text, type, vector) tuples.
        items = [
            (text, label, vec) for (text, label), vec in embedding_items
        ]

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                "Эмбеддинги", total=(len(items) + embedding_batch - 1) // embedding_batch
            )
            for i in range(0, len(items), embedding_batch):
                batch = items[i : i + embedding_batch]
                await client.set_embeddings_batch(batch)
                progress.advance(task)

        console.print(
            f"\n[bold green]Готово.[/bold green] "
            f"Сущностей: [cyan]{total_entities}[/cyan], "
            f"связей: [cyan]{total_relations}[/cyan], "
            f"эмбеддингов: [cyan]{len(items)}[/cyan], "
            f"пропущено документов: [yellow]{skipped}[/yellow]"
        )

    async def _run() -> None:
        try:
            await _load()
        finally:
            await client.close()

    asyncio.run(_run())


if __name__ == "__main__":
    app()
