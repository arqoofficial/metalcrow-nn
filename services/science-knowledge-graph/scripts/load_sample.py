"""CLI script: load sample documents into the knowledge graph via the API."""

import json
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer()
console = Console()

API_BASE = "http://localhost:8000/api/v1"


@app.command()
def load(
    data_file: Path = typer.Argument(Path("data/sample_docs.json")),
    api_url: str = typer.Option(API_BASE, "--api"),
):
    """Load JSON documents into the Science KG via batch ingestion endpoint."""
    docs = json.loads(data_file.read_text(encoding="utf-8"))
    console.print(f"[bold]Загружаю {len(docs)} документов...[/bold]")

    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{api_url}/documents/batch", json=docs)
        resp.raise_for_status()
        results = resp.json()

    table = Table("doc_id", "сущности", "связи", title="Результаты загрузки")
    for r in results:
        table.add_row(r["doc_id"], str(len(r["entities"])), str(len(r["relations"])))
    console.print(table)


@app.command()
def query(
    material: str = typer.Option(None, "--material", "-m"),
    regime: str = typer.Option(None, "--regime", "-r"),
    prop: str = typer.Option(None, "--property", "-p"),
    api_url: str = typer.Option(API_BASE, "--api"),
):
    """Query the graph: что делали с материалом X при режиме Y?"""
    params = {}
    if material:
        params["material"] = material
    if regime:
        params["regime"] = regime
    if prop:
        params["property"] = prop

    if not params:
        console.print(
            "[red]Укажите хотя бы один из: --material, --regime, --property[/red]"
        )
        raise typer.Exit(1)

    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{api_url}/search", params=params)
        resp.raise_for_status()
        result = resp.json()

    console.print(f"\n[bold green]Узлы ({len(result['nodes'])}):[/bold green]")
    for node in result["nodes"]:
        console.print(
            f"  [{node['type']}] {node['text']}  ← {', '.join(node['sources'])}"
        )

    console.print(f"\n[bold blue]Связи ({len(result['edges'])}):[/bold blue]")
    for edge in result["edges"]:
        console.print(
            f"  {edge['source']} --[{edge['relation']}]--> {edge['target']}  (verb: {edge['verb']})"
        )

    if result["gaps"]:
        console.print("\n[bold yellow]Пробелы в данных:[/bold yellow]")
        for gap in result["gaps"]:
            console.print(f"  ⚠ {gap}")


if __name__ == "__main__":
    app()
