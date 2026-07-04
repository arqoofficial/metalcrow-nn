"""One-time bulk ingestion of nornickel-2026-parser's SHARED/ corpus into the
knowledge graph via the batch API. Not a live/recomputed pipeline — run once
(rerunnable/resumable), results persist in Neo4j; SHARED/ is not re-walked on
every service start.

Walks nornickel-2026-parser's `/files/tree` under `RAW_DATA/` and
`UPLOAD_DATA/`, fetches each file's stage-0/1 OKF markdown via `/markdown`
(skipping files with no OKF output yet), chunks it like ingest_corpus.py, and
POSTs batches to /api/v1/documents/batch. Each chunk's `meta.source_path`
records the raw SHARED path so a later `GET /documents/{doc_id}` can resolve a
RAG answer's source back to a downloadable original file (see
backend/app/services/science_kg_client.py::get_document and
backend/app/api/routes/graph_articles.py).

Usage (run inside the science-knowledge-graph container, on `metalcrow-net`
so it can reach nornickel-2026-parser at `http://parser-main:8114`):
    docker compose exec science-knowledge-graph \
        python scripts/ingest_shared_corpus.py --limit 10
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
_CONCURRENCY = 6
_TREE_ROOTS = ("RAW_DATA", "UPLOAD_DATA")


async def _walk_tree(client: httpx.AsyncClient, parser_api: str, root: str) -> list[str]:
    """Enumerate raw file paths (e.g. 'RAW_DATA/reports/q1.pdf') under one
    SHARED/ subtree. Only the requested root's direct children are paginated
    server-side; nested subdirectories within max_depth already come back
    fully expanded in the same response (see
    nornickel-2026-parser's app/presentation/tree.py::_wrap_under_shared)."""
    out: list[str] = []
    offset = 0
    while True:
        resp = await client.get(
            f"{parser_api}/files/tree",
            params={
                "root": root,
                "max_depth": 10,
                "include_files": True,
                "include_dirs": True,
                "offset": offset,
                "limit": 1000,
            },
        )
        if resp.status_code == 404:
            return out  # root subtree doesn't exist (e.g. no uploads yet)
        resp.raise_for_status()
        data = resp.json()

        # Response tree is wrapped as SHARED -> <resolved_root> -> paginated
        # direct children (each subtree fully expanded up to max_depth).
        resolved_root = data["resolved_root"]
        for child in _paginated_children(data["tree"], resolved_root):
            prefix = f"{resolved_root}/{child['name']}" if resolved_root else child["name"]
            if child["type"] == "file":
                out.append(prefix)
            else:
                _collect_files(child.get("children") or [], prefix, out)

        if not data["has_more"]:
            break
        offset = data["next_offset"]
    return out


def _paginated_children(tree: dict, resolved_root: str) -> list[dict]:
    """Direct children of resolved_root in a /files/tree response page."""
    node = tree
    for part in resolved_root.split("/") if resolved_root else []:
        children = node.get("children") or []
        match = next((c for c in children if c["name"] == part), None)
        if match is None:
            return []
        node = match
    return node.get("children") or []


def _collect_files(nodes: list[dict], prefix: str, out: list[str]) -> None:
    for n in nodes:
        path = f"{prefix}/{n['name']}" if prefix else n["name"]
        if n["type"] == "file":
            out.append(path)
        else:
            _collect_files(n.get("children") or [], path, out)


async def _fetch_okf_text(
    client: httpx.AsyncClient, parser_api: str, raw_path: str
) -> tuple[str, str] | None:
    """Return (resolved_raw_path, markdown) or None when not ingestable."""

    async def _try(path: str) -> str | None:
        resp = await client.get(f"{parser_api}/markdown", params={"okf_path": path})
        if resp.status_code in (400, 404):
            return None
        resp.raise_for_status()
        return resp.text

    text = await _try(raw_path)
    if text is not None:
        return raw_path, text

    # Fallback for legacy/wrong paths missing an intermediate folder segment.
    parts = raw_path.split("/")
    if len(parts) >= 2 and parts[0] == "RAW_DATA" and parts[1] != "Доклады":
        for subdir in ("Доклады", "Обзоры", "Журналы"):
            alt = f"RAW_DATA/{subdir}/{parts[-1]}"
            if alt != raw_path:
                text = await _try(alt)
                if text is not None:
                    return alt, text
    return None


async def _ingest_async(
    parser_api: str,
    api_url: str,
    progress_file: Path,
    batch_size: int,
    chunk_chars: int,
    concurrency: int,
    limit: int | None,
) -> None:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as parser_client:
        raw_paths: list[str] = []
        for root in _TREE_ROOTS:
            raw_paths.extend(await _walk_tree(parser_client, parser_api, root))
        raw_paths.sort()
        console.print(f"[bold]{len(raw_paths)} raw files found under SHARED/[/bold]")

        if limit is not None:
            raw_paths = raw_paths[:limit]
            console.print(f"[dim]--limit {limit}: capped to {len(raw_paths)} files[/dim]")

        done = load_progress(progress_file)
        console.print(f"[dim]{len(done)} chunks already done (resuming)[/dim]")

        fetch_semaphore = asyncio.Semaphore(concurrency)

        async def _bounded_fetch(raw_path: str) -> tuple[str, tuple[str, str] | None]:
            async with fetch_semaphore:
                return raw_path, await _fetch_okf_text(parser_client, parser_api, raw_path)

        fetched = await asyncio.gather(*(_bounded_fetch(p) for p in raw_paths))

    all_docs: list[dict] = []
    skipped = 0
    for _tree_path, result in fetched:
        if result is None:
            skipped += 1
            continue
        source_path, text = result
        for i, chunk in enumerate(chunk_text(text, chunk_chars)):
            doc_id = f"{source_path}::chunk{i}"
            if doc_id in done:
                continue
            all_docs.append(
                {
                    "doc_id": doc_id,
                    "text": chunk,
                    "meta": {
                        "source_path": source_path,
                        "origin": "nornickel-2026-parser",
                        "filename": Path(source_path).name,
                    },
                }
            )
    console.print(f"[dim]{skipped} files skipped (no OKF output yet)[/dim]")

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
    parser_api: str = typer.Option(
        "http://parser-main:8114/api/v1",
        "--parser-api",
        help="nornickel-2026-parser base URL (reachable over metalcrow-net).",
    ),
    api_url: str = typer.Option("http://localhost:8000/api/v1", "--api"),
    progress_file: Path = typer.Option(
        Path("scripts/.ingest_shared_progress.json"), "--progress-file"
    ),
    batch_size: int = typer.Option(_BATCH_SIZE, "--batch-size"),
    chunk_chars: int = typer.Option(_CHUNK_CHARS, "--chunk-chars"),
    concurrency: int = typer.Option(_CONCURRENCY, "--concurrency"),
    limit: int = typer.Option(
        None,
        "--limit",
        help="Cap the number of raw files ingested (e.g. --limit 10 for a smoke test).",
    ),
):
    """Walk nornickel-2026-parser's SHARED/ (RAW_DATA + UPLOAD_DATA), ingest
    every file with OKF markdown output into the knowledge graph. Chunked and
    resumable via progress_file, same as ingest_corpus.py."""
    asyncio.run(
        _ingest_async(
            parser_api,
            api_url,
            progress_file,
            batch_size,
            chunk_chars,
            concurrency,
            limit,
        )
    )


if __name__ == "__main__":
    app()
