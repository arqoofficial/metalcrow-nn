"""Pre-compute OpenAI embeddings for spaCy-extracted facts.

Reads ``SHARED/facts/<stem>.json`` (entities + relations) and builds an offline
vector index in ``SHARED/vectors/``:

    entities.npy        float32 [N_ent, 1536], L2-normalised
    entities.jsonl      one JSON object per line with id, text, label, …
    relations.npy       float32 [N_rel, 1536]
    relations.jsonl     one JSON object per line with id, text, relation, …
    manifest.json       model, dim, normalized, counts, built_at, git_sha

The index is meant to be built once and then loaded by downstream search/RAG
components, instead of re-computing embeddings on every service boot.

Run inside the science-knowledge-graph folder:

    PYTHONPATH=. .venv/bin/python scripts/embed_facts.py \
        ../nornickel-2026-parser/SHARED/facts \
        --output ../nornickel-2026-parser/SHARED/vectors \
        --batch-size 128

With ``--skip-existing`` the script skips kinds whose ``.npy`` + ``.jsonl`` are
already present, so incremental runs only embed newly-added facts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import subprocess
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
from science_kg.embeddings import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, embed_batch
from science_kg.models import EntityType
from science_kg.nlp.normalizer import canonical_material, canonical_process

app = typer.Typer(help="Pre-compute embeddings for spaCy-extracted facts.")
console = Console()
logger = logging.getLogger(__name__)

# Labels that existed in older fact JSON dumps before the EntityType rename.
_LEGACY_LABEL_MAP: dict[str, str] = {
    "REGIME": "PROCESS",
    "VALUE": "PROPERTY",
}


def _normalize_label(label: str) -> str | None:
    """Map legacy labels to the current EntityType schema; drop unknown ones."""
    label = _LEGACY_LABEL_MAP.get(label, label)
    try:
        EntityType(label)
    except ValueError:
        return None
    return label


def _canonical_entity(text: str, label: str) -> str:
    """Return a canonical string for deduplication of entities."""
    if label == "MATERIAL":
        return canonical_material(text)
    if label in ("PROCESS", "REGIME"):
        return canonical_process(text)
    return text.strip().lower()


def _is_valid_entity(text: str, label: str | None) -> bool:
    """Drop one-character spans and entities without a known label."""
    if label is None:
        return False
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    return True


def _verbalize_relation(rel: dict[str, Any]) -> str:
    """Turn a relation triple into a short semantic phrase for embedding."""
    verb = (rel.get("verb") or "").strip()
    source = rel.get("source", "").strip()
    target = rel.get("target", "").strip()
    relation = rel.get("relation", "").strip()
    if verb:
        return f"{source} {verb} {target}".strip()
    return f"{source} — {relation} — {target}".strip()


def _load_facts(facts_dir: Path) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    """Scan all JSON facts and return deduplicated entities + relations.

    Entity key is ``(canonical_text, label)``. Relation key is the normalized
    verbalized phrase. Metadata keeps ``source_docs`` (deduplicated, insertion
    order) and the first seen ``start_char``/``end_char`` for entities.
    """
    fact_files = sorted(facts_dir.rglob("*.json"))
    if not fact_files:
        console.print(f"[yellow]Нет .json файлов в {facts_dir}[/yellow]")
        raise typer.Exit(0)

    console.print(f"[bold]Сканирование {len(fact_files)} facts-файлов…[/bold]")

    entities: dict[tuple[str, str], dict[str, Any]] = {}
    relations: dict[str, dict[str, Any]] = {}

    for path in fact_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("skip malformed %s: %s", path, exc)
            continue

        source_doc = data.get("doc_id") or path.stem

        for raw_ent in data.get("entities", []):
            text = raw_ent.get("text", "").strip()
            label = _normalize_label(raw_ent.get("label", ""))
            if not _is_valid_entity(text, label):
                continue
            canonical = _canonical_entity(text, label)
            key = (canonical, label)
            if key not in entities:
                entities[key] = {
                    "text": canonical,
                    "label": label,
                    "surface_forms": [],
                    "source_docs": [],
                    "start_char": raw_ent.get("start_char"),
                    "end_char": raw_ent.get("end_char"),
                    "n_occurrences": 0,
                }
            ent = entities[key]
            ent["n_occurrences"] += 1
            if source_doc not in ent["source_docs"]:
                ent["source_docs"].append(source_doc)
            surface = text
            if surface not in ent["surface_forms"]:
                ent["surface_forms"].append(surface)

        for raw_rel in data.get("relations", []):
            phrase = _verbalize_relation(raw_rel)
            if not phrase or len(phrase) < 2:
                continue
            # Light normalization for dedup: lowercase, collapse whitespace.
            key = " ".join(phrase.split()).lower()
            if key not in relations:
                relations[key] = {
                    "text": phrase,
                    "relation": raw_rel.get("relation", ""),
                    "source_docs": [],
                    "n_occurrences": 0,
                }
            rel = relations[key]
            rel["n_occurrences"] += 1
            if source_doc not in rel["source_docs"]:
                rel["source_docs"].append(source_doc)

    return entities, relations


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """L2-normalize each row in-place (safe for all-zero rows)."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    np.divide(vectors, norms, out=vectors, where=norms != 0)
    return vectors


def _git_sha() -> str | None:
    """Return the current git SHA for reproducibility, or None."""
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parents[2],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return None


def _save_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


async def _embed_items(
    items: list[dict[str, Any]],
    batch_size: int,
    max_concurrent: int,
    kind: str,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Embed the ``text`` field of each item and return (vectors, items).

    Items whose embedding fails are dropped from the returned arrays so the
    saved index never contains ``None`` vectors.
    """
    texts = [item["text"] for item in items]
    all_vectors: list[np.ndarray] = []
    kept_items: list[dict[str, Any]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        total_batches = math.ceil(len(texts) / batch_size)
        task = progress.add_task(f"Эмбеддинг {kind}", total=total_batches)
        semaphore = asyncio.Semaphore(max(1, max_concurrent))

        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i : i + batch_size]
            batch_items = items[i : i + batch_size]
            async with semaphore:
                vectors = await embed_batch(batch_texts)
            for vec, item in zip(vectors, batch_items):
                if vec is None:
                    continue
                all_vectors.append(np.asarray(vec, dtype=np.float32))
                # Assign id = row index in the final array.
                item = dict(item)
                item["id"] = len(kept_items)
                kept_items.append(item)
            progress.advance(task)

    if not all_vectors:
        return np.zeros((0, EMBEDDING_DIMENSIONS), dtype=np.float32), []

    matrix = np.vstack(all_vectors)
    return _l2_normalize(matrix), kept_items


@app.command()
def main(
    input_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Папка с facts JSON (например ../nornickel-2026-parser/SHARED/facts).",
    ),
    output_dir: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Папка для векторных артефактов (по умолчанию: <input_dir>/../vectors).",
    ),
    batch_size: int = typer.Option(
        128,
        "--batch-size",
        help="Количество текстов в одном запросе к /embeddings.",
    ),
    max_concurrent: int = typer.Option(
        5,
        "--max-concurrent",
        help="Одновременных запросов к API (rate-limit guard).",
    ),
    skip_existing: bool = typer.Option(
        False,
        "--skip-existing",
        help="Пропустить вид, для которого уже есть .npy + .jsonl.",
    ),
) -> None:
    """Build an offline embedding index over spaCy-extracted facts."""
    if not settings.openai_api_key:
        console.print(
            "[bold red]Ошибка:[/bold red] OPENAI_API_KEY не задан. "
            "Установите переменную окружения или .env."
        )
        raise typer.Exit(1)

    if output_dir is None:
        output_dir = input_dir.parent / "vectors"
    output_dir.mkdir(parents=True, exist_ok=True)

    entities, relations = _load_facts(input_dir)
    console.print(
        f"[bold]Уникальных единиц:[/bold] "
        f"сущностей [cyan]{len(entities)}[/cyan], "
        f"связей [cyan]{len(relations)}[/cyan]\n"
    )

    manifest: dict[str, Any] = {
        "model": EMBEDDING_MODEL,
        "dim": EMBEDDING_DIMENSIONS,
        "normalized": True,
        "input_dir": str(input_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "built_at": datetime.now(tz=UTC).isoformat(),
        "git_sha": _git_sha(),
    }

    # Prepare entity rows preserving a stable order.
    entity_items = [
        {
            "text": ent["text"],
            "label": ent["label"],
            "n_occurrences": ent["n_occurrences"],
            "source_docs": ent["source_docs"],
            "surface_forms": ent["surface_forms"],
            "start_char": ent["start_char"],
            "end_char": ent["end_char"],
        }
        for ent in entities.values()
    ]
    entity_items.sort(key=lambda x: (x["label"], x["text"]))

    entity_npy = output_dir / "entities.npy"
    entity_jsonl = output_dir / "entities.jsonl"
    if skip_existing and entity_npy.exists() and entity_jsonl.exists():
        console.print("[yellow]entities уже существуют — пропуск (--skip-existing).[/yellow]")
        entity_rows = _load_jsonl(entity_jsonl)
        manifest["entities"] = {"count": len(entity_rows)}
    else:
        ent_vectors, entity_rows = asyncio.run(
            _embed_items(entity_items, batch_size, max_concurrent, "сущностей")
        )
        np.save(entity_npy, ent_vectors)
        _save_jsonl(entity_rows, entity_jsonl)
        manifest["entities"] = {"count": len(entity_rows)}
        console.print(
            f"[green]✓ entities[/green]: {len(entity_rows)} векторов → {entity_npy}"
        )

    # Prepare relation rows.
    relation_items = [
        {
            "text": rel["text"],
            "relation": rel["relation"],
            "n_occurrences": rel["n_occurrences"],
            "source_docs": rel["source_docs"],
        }
        for rel in relations.values()
    ]
    relation_items.sort(key=lambda x: x["text"])

    relation_npy = output_dir / "relations.npy"
    relation_jsonl = output_dir / "relations.jsonl"
    if skip_existing and relation_npy.exists() and relation_jsonl.exists():
        console.print("[yellow]relations уже существуют — пропуск (--skip-existing).[/yellow]")
        relation_rows = _load_jsonl(relation_jsonl)
        manifest["relations"] = {"count": len(relation_rows)}
    else:
        rel_vectors, relation_rows = asyncio.run(
            _embed_items(relation_items, batch_size, max_concurrent, "связей")
        )
        np.save(relation_npy, rel_vectors)
        _save_jsonl(relation_rows, relation_jsonl)
        manifest["relations"] = {"count": len(relation_rows)}
        console.print(
            f"[green]✓ relations[/green]: {len(relation_rows)} векторов → {relation_npy}"
        )

    # Write manifest last so a consumer knows the index is complete.
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"\n[bold green]Готово.[/bold green] Манифест: {manifest_path}")


if __name__ == "__main__":
    app()
