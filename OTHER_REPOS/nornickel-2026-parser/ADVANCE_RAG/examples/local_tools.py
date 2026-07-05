"""Local LangChain-style tool wrappers calling REST /api/v1/query."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import yaml
from langchain_core.tools import tool

TOOL_TYPE_MAP = {
    "simple_rag": "dense",
    "sparse_rag": "sparse",
    "advance_rag": "advance",
    "advance_rag_fast": "RRF",
    "grep_rag": "fuzzy",
}


def load_config() -> dict[str, Any]:
    config_path = Path(__file__).resolve().parent / "config.yaml"
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def _call_query(
    tool_name: str,
    query: str,
    source_subfolder: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    config = load_config()
    body: dict[str, Any] = {
        "query": query,
        "type": TOOL_TYPE_MAP[tool_name],
        "source_subfolder": source_subfolder or config["advancerag"]["default_source_subfolder"],
        "limit": limit or config["advancerag"]["default_limit"],
    }
    with httpx.Client(timeout=config["api"]["timeout_sec"]) as client:
        response = client.post(f"{config['api']['base_url']}/query", json=body)
        response.raise_for_status()
        return response.json()


@tool
def simple_rag(
    query: str,
    source_subfolder: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Local dense retrieval wrapper for ADVANCE_RAG."""
    return _call_query("simple_rag", query, source_subfolder, limit)


@tool
def sparse_rag(
    query: str,
    source_subfolder: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Local sparse retrieval wrapper for ADVANCE_RAG."""
    return _call_query("sparse_rag", query, source_subfolder, limit)


@tool
def advance_rag(
    query: str,
    source_subfolder: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Local advance retrieval wrapper for ADVANCE_RAG."""
    return _call_query("advance_rag", query, source_subfolder, limit)


@tool
def advance_rag_fast(
    query: str,
    source_subfolder: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Local RRF retrieval wrapper for ADVANCE_RAG."""
    return _call_query("advance_rag_fast", query, source_subfolder, limit)


@tool
def grep_rag(
    query: str,
    source_subfolder: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Local fuzzy retrieval wrapper for ADVANCE_RAG."""
    return _call_query("grep_rag", query, source_subfolder, limit)
