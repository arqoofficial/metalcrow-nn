"""Shared helpers for bulk-ingestion scripts (`ingest_corpus.py`,
`ingest_shared_corpus.py`) — paragraph-boundary chunking, resumable
progress tracking, and batch POSTing to `/documents/batch` with retry-on-
transient-Neo4j-deadlock. Extracted so both scripts share the same proven
concurrency/resume machinery instead of duplicating it."""

import asyncio
import json
from pathlib import Path

import httpx
from rich.console import Console

console = Console()

_MAX_RETRIES = 4


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Greedy paragraph-boundary chunking; hard-splits any single paragraph
    that alone exceeds max_chars (rare, but some markdown tables are huge)."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        if current:
            chunks.append("\n\n".join(current))

    for para in paragraphs:
        if len(para) > max_chars:
            flush()
            current, current_len = [], 0
            for i in range(0, len(para), max_chars):
                chunks.append(para[i : i + max_chars])
            continue
        if current_len + len(para) > max_chars and current:
            flush()
            current, current_len = [], 0
        current.append(para)
        current_len += len(para) + 2

    flush()
    return [c for c in chunks if c.strip()]


def load_progress(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")))


def save_progress(path: Path, done: set[str]) -> None:
    path.write_text(json.dumps(sorted(done), ensure_ascii=False), encoding="utf-8")


async def run_batch(
    client: httpx.AsyncClient,
    api_url: str,
    batch: list[dict],
    done: set[str],
    progress_file: Path,
    counters: dict[str, int],
) -> None:
    """Concurrent batches can MERGE the same shared entity node (e.g. a common
    material mentioned across many docs) at the same time — Neo4j's Community
    Edition lock manager resolves that as a transient deadlock (500 on our
    side), not a real failure. Retry with backoff; a fresh transaction almost
    always succeeds once the colliding one has committed."""
    resp = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = await client.post(f"{api_url}/documents/batch", json=batch)
            resp.raise_for_status()
            break
        except httpx.HTTPError as exc:
            if attempt == _MAX_RETRIES - 1:
                console.print(
                    f"[red]batch failed after {_MAX_RETRIES} attempts "
                    f"({len(batch)} chunks): {exc}[/red]"
                )
                return
            await asyncio.sleep(2**attempt)

    for d in batch:
        done.add(d["doc_id"])
    save_progress(progress_file, done)

    results = resp.json()
    n_ent = sum(len(r["entities"]) for r in results)
    n_rel = sum(len(r["relations"]) for r in results)
    counters["done"] += len(batch)
    console.print(
        f"[green]{counters['done']}/{counters['total']}[/green] "
        f"(+{n_ent} entities, +{n_rel} relations this batch)"
    )
