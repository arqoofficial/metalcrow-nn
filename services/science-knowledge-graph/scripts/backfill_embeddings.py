"""CLI script: compute embeddings for entities ingested before the
entity_embedding_idx feature existed. Unlike scripts/load_sample.py, this
talks to Neo4j and the embeddings API directly (not through the HTTP API) —
there's deliberately no admin API endpoint for a one-off backfill operation.

Run inside the container:
    docker compose exec science-knowledge-graph uv run python -m scripts.backfill_embeddings
"""

import asyncio

import typer
from rich.console import Console

from science_kg.config import settings
from science_kg.embeddings import embed_text
from science_kg.graph.neo4j_client import Neo4jClient

app = typer.Typer()
console = Console()


async def _backfill(limit: int) -> None:
    """Single pass, not a retry loop: a node whose embedding fails (e.g. no
    OPENAI_API_KEY) stays `embedding IS NULL` forever, so looping until
    "no nodes missing" would never terminate if any failure is persistent
    rather than transient. `--limit` large enough for one run to cover a
    demo-scale graph; re-run the command if there's more than that."""
    client = Neo4jClient(
        settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password
    )
    total = 0
    failed = 0
    try:
        nodes = await client.list_entities_missing_embedding(limit=limit)
        if not nodes:
            console.print("[bold]Nothing to backfill.[/bold]")
            return

        for node in nodes:
            vector = await embed_text(node.text)
            if vector is None:
                failed += 1
                console.print(
                    f"  [yellow]skip[/yellow] [{node.type}] {node.text} "
                    "(embedding failed)"
                )
                continue
            await client.set_embedding(node.text, node.type, vector)
            total += 1
            console.print(f"  [green]ok[/green]   [{node.type}] {node.text}")
    finally:
        await client.close()

    console.print(f"\n[bold]Done.[/bold] backfilled={total} skipped={failed}")
    if nodes and len(nodes) == limit:
        console.print(
            "[yellow]Hit --limit — there may be more nodes left; re-run to continue.[/yellow]"
        )


@app.command()
def run(limit: int = typer.Option(2000, "--limit")):
    """Find :Entity nodes with no `embedding` yet and compute+set one, in a
    single pass (see `_backfill` docstring for why not a retry loop)."""
    asyncio.run(_backfill(limit))


if __name__ == "__main__":
    app()
