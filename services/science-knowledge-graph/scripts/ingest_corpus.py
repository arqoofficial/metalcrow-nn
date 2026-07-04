"""One-time bulk ingestion of a real markdown corpus (e.g. RAW_DATA_422) into
the knowledge graph via the batch API. Not a live/recomputed pipeline —
run once, results persist in Neo4j (see compose.yml `neo4j-data` volume).

Chunks each file (median ~740KB, up to ~4.5MB — exceeds spaCy's default
nlp.max_length of 1,000,000 chars on the large ones) into ~20K-char pieces on
paragraph boundaries, POSTs small batches to /api/v1/documents/batch, and
tracks progress in a JSON file so an interrupted run can be resumed without
reprocessing already-ingested chunks.

Usage (run inside the science-knowledge-graph container, which has no
published host port):
    docker compose cp RAW_DATA_422/RAW_DATA science-knowledge-graph:/data/raw
    docker compose exec science-knowledge-graph \
        python scripts/ingest_corpus.py /data/raw
"""

import asyncio
from pathlib import Path

import httpx
import typer

from _ingest_lib import (
    chunk_text,
    console,
    load_progress,
    run_batch,
)

app = typer.Typer()

_CHUNK_CHARS = 20_000
_BATCH_SIZE = 8
_TIMEOUT = 600
_CONCURRENCY = 6  # concurrent in-flight /documents/batch requests — the
# server's default ThreadPoolExecutor (spaCy nlp.pipe is CPU-bound) has
# headroom on multi-core hosts; sequential requests here left cores idle


async def _ingest_async(
    corpus_dir: Path,
    api_url: str,
    progress_file: Path,
    batch_size: int,
    chunk_chars: int,
    concurrency: int,
) -> None:
    files = sorted(corpus_dir.rglob("*.md"))
    console.print(f"[bold]{len(files)} markdown files under {corpus_dir}[/bold]")

    done = load_progress(progress_file)
    console.print(f"[dim]{len(done)} chunks already done (resuming)[/dim]")

    all_docs: list[dict] = []
    for path in files:
        rel = str(path.relative_to(corpus_dir))
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, chunk in enumerate(chunk_text(text, chunk_chars)):
            doc_id = f"{rel}::chunk{i}"
            if doc_id in done:
                continue
            all_docs.append({"doc_id": doc_id, "text": chunk, "meta": {}})

    total = len(all_docs)
    console.print(f"[bold]{total} chunks to ingest[/bold]")
    if total == 0:
        console.print("[green]Nothing to do.[/green]")
        return

    batches = [
        all_docs[start : start + batch_size] for start in range(0, total, batch_size)
    ]
    counters = {"done": 0, "total": total}
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded(batch: list[dict]) -> None:
        async with semaphore:
            await run_batch(client, api_url, batch, done, progress_file, counters)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        await asyncio.gather(*(_bounded(b) for b in batches))

    console.print(f"[bold green]Done. {len(done)} chunks ingested total.[/bold green]")


@app.command()
def ingest(
    corpus_dir: Path = typer.Argument(...),
    api_url: str = typer.Option("http://localhost:8000/api/v1", "--api"),
    progress_file: Path = typer.Option(
        Path("scripts/.ingest_progress.json"), "--progress-file"
    ),
    batch_size: int = typer.Option(_BATCH_SIZE, "--batch-size"),
    chunk_chars: int = typer.Option(_CHUNK_CHARS, "--chunk-chars"),
    concurrency: int = typer.Option(_CONCURRENCY, "--concurrency"),
):
    """Recursively ingest every .md file under corpus_dir, chunked and
    resumable via progress_file. Sends up to `concurrency` batches at once."""
    asyncio.run(
        _ingest_async(
            corpus_dir, api_url, progress_file, batch_size, chunk_chars, concurrency
        )
    )


if __name__ == "__main__":
    app()
