"""Typer + Rich operator panel for ADVANCE_RAG."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="ADVANCE_RAG operator panel")
console = Console()

DEFAULT_API_BASE = "http://127.0.0.1:8115"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8115
PID_FILE = Path(".run/advancerag.pid")
MCP_PID_FILE = Path(".run/advancerag-mcp.pid")


def _api_base() -> str:
    return os.getenv("API_BASE_URL", DEFAULT_API_BASE)


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _read_pid_file(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="utf-8")


def _write_pid_file(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def _remove_pid() -> None:
    if PID_FILE.exists():
        PID_FILE.unlink()


def _remove_pid_file(path: Path) -> None:
    if path.exists():
        path.unlink()


def _pid_cmdline(pid: int) -> str:
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if not proc_cmdline.exists():
        return ""
    raw = proc_cmdline.read_text(encoding="utf-8", errors="ignore")
    return raw.replace("\x00", " ").strip()


def _pid_matches(pid: int, expected_tokens: list[str]) -> bool:
    cmdline = _pid_cmdline(pid)
    if not cmdline:
        return False
    return all(token in cmdline for token in expected_tokens)


def _request(method: str, path: str, json_payload: dict | None = None) -> tuple[int, str]:
    url = f"{_api_base()}{path}"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.request(method, url, json=json_payload)
            return response.status_code, response.text
    except Exception as exc:  # pragma: no cover - defensive
        return 0, str(exc)


def _request_json(method: str, path: str) -> tuple[int, dict[str, Any] | str]:
    code, body = _request(method, path)
    if code == 0 or not body:
        return code, body
    try:
        return code, json.loads(body)
    except json.JSONDecodeError:
        return code, body


def _append_runtime_rows(table: Table, runtime_body: dict[str, Any]) -> None:
    queue = runtime_body.get("queue", {})
    chroma = runtime_body.get("chroma", {})
    embedding = runtime_body.get("dense_embedding", {})
    table.add_row("queue/backend", str(queue.get("backend", "n/a")), "")
    table.add_row("queue/size", str(queue.get("size", "n/a")), "pending index_path jobs")
    table.add_row("queue/failed", str(queue.get("failed_count", "n/a")), "failed worker jobs")
    table.add_row(
        "chroma/documents",
        str(chroma.get("document_count", "n/a")),
        f"collection={chroma.get('collection_name', 'n/a')}",
    )
    table.add_row(
        "dense_embedding/model",
        str(embedding.get("model", "n/a")),
        f"{embedding.get('mode', 'n/a')} via {embedding.get('provider', 'n/a')}",
    )


@app.command()
def status() -> None:
    """Show health, readiness, queue size, and dense embedding model."""
    health_code, health_body = _request("GET", "/health")
    ready_code, ready_body = _request("GET", "/ready")
    runtime_code, runtime_body = _request_json("GET", "/admin/runtime")
    table = Table(title="ADVANCE_RAG Status")
    table.add_column("Probe")
    table.add_column("Status")
    table.add_column("Body")
    table.add_row("/health", str(health_code), health_body[:120])
    table.add_row("/ready", str(ready_code), ready_body[:120])
    if isinstance(runtime_body, dict):
        _append_runtime_rows(table, runtime_body)
    else:
        table.add_row("/admin/runtime", str(runtime_code), str(runtime_body)[:120])
    table.add_row("pid_file", str(PID_FILE.exists()), str(_read_pid()))
    table.add_row("mcp_pid_file", str(MCP_PID_FILE.exists()), str(_read_pid_file(MCP_PID_FILE)))
    console.print(table)


@app.command()
def start(
    host: str = typer.Option(DEFAULT_HOST, help="Uvicorn host"),
    port: int = typer.Option(DEFAULT_PORT, help="Uvicorn port"),
    with_mcp: bool = typer.Option(False, help="Also start MCP server process"),
) -> None:
    """Start service in background on host machine."""
    existing = _read_pid()
    if existing is not None:
        raise typer.Exit(code=0)
    cmd = [
        "uv",
        "run",
        "uvicorn",
        "app.main:create_app",
        "--factory",
        "--host",
        host,
        "--port",
        str(port),
    ]
    process = subprocess.Popen(cmd)  # noqa: S603
    _write_pid(process.pid)
    console.print(f"Started ADVANCE_RAG pid={process.pid}")
    if with_mcp:
        mcp_cmd = ["uv", "run", "python", "-m", "app.mcp_server"]
        mcp_process = subprocess.Popen(mcp_cmd)  # noqa: S603
        _write_pid_file(MCP_PID_FILE, mcp_process.pid)
        console.print(f"Started MCP server pid={mcp_process.pid}")


@app.command()
def stop() -> None:
    """Stop background service started by panel."""
    pid = _read_pid()
    if pid is None:
        console.print("No PID file found.")
        raise typer.Exit(code=0)
    try:
        if _pid_matches(pid, ["uvicorn", "app.main"]):
            os.kill(pid, signal.SIGTERM)
            console.print(f"Stopped ADVANCE_RAG pid={pid}")
        else:
            console.print(f"Refusing to stop pid={pid}: command line does not match ADVANCE_RAG")
    except ProcessLookupError:
        console.print(f"Process not found pid={pid}")
    _remove_pid()

    mcp_pid = _read_pid_file(MCP_PID_FILE)
    if mcp_pid is not None:
        try:
            if _pid_matches(mcp_pid, ["python", "app.mcp_server"]):
                os.kill(mcp_pid, signal.SIGTERM)
                console.print(f"Stopped MCP server pid={mcp_pid}")
            else:
                console.print(
                    "Refusing to stop MCP pid="
                    f"{mcp_pid}: command line does not match app.mcp_server"
                )
        except ProcessLookupError:
            console.print(f"MCP process not found pid={mcp_pid}")
        _remove_pid_file(MCP_PID_FILE)


@app.command()
def rerun(
    host: str = typer.Option(DEFAULT_HOST, help="Uvicorn host"),
    port: int = typer.Option(DEFAULT_PORT, help="Uvicorn port"),
    with_mcp: bool = typer.Option(False, help="Also start MCP server process"),
) -> None:
    """Restart service on host machine."""
    stop()
    start(host=host, port=port, with_mcp=with_mcp)


@app.command("index-doc")
def index_doc(path: str) -> None:
    """Trigger /api/v1/index_doc."""
    payload = {"path": path}
    code, body = _request("POST", "/api/v1/index_doc", payload)
    console.print(f"status={code}\n{body}")


@app.command("index-path")
def index_path(path: str) -> None:
    """Trigger /api/v1/index_path."""
    payload = {"path": path}
    code, body = _request("POST", "/api/v1/index_path", payload)
    console.print(f"status={code}\n{body}")


if __name__ == "__main__":
    app()
