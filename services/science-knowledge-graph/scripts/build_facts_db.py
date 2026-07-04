"""Build a SQLite DB containing spaCy-extracted facts + precomputed OpenAI vectors.

Reads the output of ``scripts/embed_facts.py`` (``SHARED/vectors/``) plus the
source ``SHARED/facts/*.json`` and produces ``SHARED/facts.db``:

    documents   doc_id, source_file, language, char_count, n_chunks, parsed_at
    entities    id, text, label, canonical_text, embedding, n_occurrences, source_docs
    relations   id, source, target, relation, verb, phrase, embedding, n_occurrences, source_docs

``embedding`` is stored as a compact float32 blob (1536 * 4 bytes). The DB is a
single self-contained artifact: downstream search/RAG can query it without
re-running spaCy or the OpenAI embedding API.

Usage:

    PYTHONPATH=. .venv/bin/python scripts/build_facts_db.py \
        ../nornickel-2026-parser/SHARED/facts \
        ../nornickel-2026-parser/SHARED/vectors \
        --output ../nornickel-2026-parser/SHARED/facts.db
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import typer
from rich.console import Console

app = typer.Typer(help="Build a SQLite DB from facts + precomputed vectors.")
console = Console()


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
        help="Папка с векторами (entities.npy/jsonl, relations.npy/jsonl, manifest.json).",
    ),
    output: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Путь к выходному SQLite-файлу (по умолчанию <facts_dir>/../facts.db).",
    ),
) -> None:
    if output is None:
        output = facts_dir.parent / "facts.db"
    output.parent.mkdir(parents=True, exist_ok=True)

    manifest_path = vectors_dir / "manifest.json"
    if not manifest_path.exists():
        console.print(f"[red]manifest.json не найден в {vectors_dir}[/red]")
        raise typer.Exit(1)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    console.print(
        f"[bold]Векторный индекс[/bold]: model={manifest['model']}, "
        f"dim={manifest['dim']}, entities={manifest['entities']['count']}, "
        f"relations={manifest['relations']['count']}"
    )

    # Load vectors + metadata.
    entity_rows = _load_jsonl(vectors_dir / "entities.jsonl")
    entity_vectors = np.load(vectors_dir / "entities.npy")
    relation_rows = _load_jsonl(vectors_dir / "relations.jsonl")
    relation_vectors = np.load(vectors_dir / "relations.npy")

    if len(entity_rows) != entity_vectors.shape[0]:
        console.print("[red]entity shape mismatch[/red]")
        raise typer.Exit(1)
    if len(relation_rows) != relation_vectors.shape[0]:
        console.print("[red]relation shape mismatch[/red]")
        raise typer.Exit(1)

    # Build lookup tables keyed by the dedup key used in embed_facts.py.
    # For entities: (canonical_text, label).
    # For relations: normalized phrase.
    entity_by_key: dict[tuple[str, str], tuple[dict, bytes]] = {}
    for row, vec in zip(entity_rows, entity_vectors):
        key = (row["text"], row["label"])
        entity_by_key[key] = (row, vec.astype(np.float32).tobytes())

    relation_by_key: dict[str, tuple[dict, bytes]] = {}
    for row, vec in zip(relation_rows, relation_vectors):
        phrase = " ".join(row["text"].split()).lower()
        relation_by_key[phrase] = (row, vec.astype(np.float32).tobytes())

    # Re-scan facts to aggregate document metadata and to confirm every fact
    # has a matching vector (defensive check).
    fact_files = sorted(facts_dir.rglob("*.json"))
    documents: dict[str, dict] = {}
    entity_doc_hits: dict[tuple[str, str], set[str]] = defaultdict(set)
    relation_doc_hits: dict[str, set[str]] = defaultdict(set)

    for path in fact_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        doc_id = data.get("doc_id") or path.stem
        meta = data.get("metadata", {})
        documents[doc_id] = {
            "doc_id": doc_id,
            "source_file": data.get("source_file", path.name),
            "language": meta.get("language", "unknown"),
            "char_count": meta.get("char_count", 0),
            "n_chunks": meta.get("n_chunks", 0),
            "parsed_at": meta.get("parsed_at", ""),
        }
        for ent in data.get("entities", []):
            label = ent.get("label", "")
            key = (_canonical_entity(ent.get("text", ""), label), label)
            if key in entity_by_key:
                entity_doc_hits[key].add(doc_id)
        for rel in data.get("relations", []):
            phrase = _verbalize_relation(rel)
            phrase_key = " ".join(phrase.split()).lower()
            if phrase_key in relation_by_key:
                relation_doc_hits[phrase_key].add(doc_id)

    # Write SQLite DB.
    output.unlink(missing_ok=True)
    conn = sqlite3.connect(output)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.execute(
        """
        CREATE TABLE documents (
            doc_id TEXT PRIMARY KEY,
            source_file TEXT NOT NULL,
            language TEXT,
            char_count INTEGER,
            n_chunks INTEGER,
            parsed_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            label TEXT NOT NULL,
            canonical_text TEXT NOT NULL,
            embedding BLOB NOT NULL,
            n_occurrences INTEGER NOT NULL DEFAULT 0,
            source_docs TEXT NOT NULL DEFAULT '[]'
        )
        """
    )
    conn.execute("CREATE INDEX idx_entities_label ON entities(label)")
    conn.execute("CREATE INDEX idx_entities_text ON entities(text)")
    conn.execute(
        """
        CREATE TABLE relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            relation TEXT NOT NULL,
            verb TEXT,
            phrase TEXT NOT NULL,
            embedding BLOB NOT NULL,
            n_occurrences INTEGER NOT NULL DEFAULT 0,
            source_docs TEXT NOT NULL DEFAULT '[]'
        )
        """
    )
    conn.execute("CREATE INDEX idx_relations_relation ON relations(relation)")

    with conn:
        conn.executemany(
            """
            INSERT INTO documents (doc_id, source_file, language, char_count, n_chunks, parsed_at)
            VALUES (:doc_id, :source_file, :language, :char_count, :n_chunks, :parsed_at)
            """,
            documents.values(),
        )

        entity_inserts = []
        for key, (row, emb_blob) in entity_by_key.items():
            docs = sorted(entity_doc_hits.get(key, []))
            entity_inserts.append(
                {
                    "text": row["text"],
                    "label": row["label"],
                    "canonical_text": row["text"],
                    "embedding": emb_blob,
                    "n_occurrences": row.get("n_occurrences", 0),
                    "source_docs": json.dumps(docs, ensure_ascii=False),
                }
            )
        conn.executemany(
            """
            INSERT INTO entities (text, label, canonical_text, embedding, n_occurrences, source_docs)
            VALUES (:text, :label, :canonical_text, :embedding, :n_occurrences, :source_docs)
            """,
            entity_inserts,
        )

        relation_inserts = []
        for key, (row, emb_blob) in relation_by_key.items():
            docs = sorted(relation_doc_hits.get(key, []))
            relation_inserts.append(
                {
                    "source": "",
                    "target": "",
                    "relation": row.get("relation", ""),
                    "verb": "",
                    "phrase": row["text"],
                    "embedding": emb_blob,
                    "n_occurrences": row.get("n_occurrences", 0),
                    "source_docs": json.dumps(docs, ensure_ascii=False),
                }
            )
        conn.executemany(
            """
            INSERT INTO relations (source, target, relation, verb, phrase, embedding, n_occurrences, source_docs)
            VALUES (:source, :target, :relation, :verb, :phrase, :embedding, :n_occurrences, :source_docs)
            """,
            relation_inserts,
        )

    # SQLite stats.
    cur = conn.execute("SELECT count(*) FROM documents")
    n_docs = cur.fetchone()[0]
    cur = conn.execute("SELECT count(*) FROM entities")
    n_ents = cur.fetchone()[0]
    cur = conn.execute("SELECT count(*) FROM relations")
    n_rels = cur.fetchone()[0]
    conn.close()

    console.print(
        f"[bold green]✓[/bold green] БД готова: [cyan]{output}[/cyan]\n"
        f"  документов: [cyan]{n_docs}[/cyan], "
        f"сущностей: [cyan]{n_ents}[/cyan], "
        f"связей: [cyan]{n_rels}[/cyan]"
    )


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _canonical_entity(text: str, label: str) -> str:
    # Must match embed_facts.py exactly.
    from science_kg.nlp.normalizer import canonical_material, canonical_process

    if label == "MATERIAL":
        return canonical_material(text)
    if label in ("PROCESS", "REGIME"):
        return canonical_process(text)
    return text.strip().lower()


def _verbalize_relation(rel: dict) -> str:
    verb = (rel.get("verb") or "").strip()
    source = rel.get("source", "").strip()
    target = rel.get("target", "").strip()
    relation = rel.get("relation", "").strip()
    if verb:
        return f"{source} {verb} {target}".strip()
    return f"{source} — {relation} — {target}".strip()


if __name__ == "__main__":
    app()
