"""Minimal MCP client example for ADVANCE_RAG retrieval tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import yaml

TOOL_NAMES = ("simple_rag", "sparse_rag", "advance_rag", "advance_rag_fast", "grep_rag")


def load_config() -> dict[str, Any]:
    config_path = Path(__file__).resolve().parent / "config.yaml"
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


class MCPRetrievalClient:
    def __init__(self, server_url: str, timeout_sec: float = 20.0) -> None:
        self._server_url = server_url.rstrip("/")
        self._timeout_sec = timeout_sec

    def call_tool(
        self,
        tool_name: str,
        query: str,
        source_subfolder: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "tool": tool_name,
            "arguments": {
                "query": query,
                "source_subfolder": source_subfolder,
                "limit": limit,
            },
        }
        with httpx.Client(timeout=self._timeout_sec) as client:
            response = client.post(self._server_url, json=payload)
            response.raise_for_status()
            return response.json()

    def call_all_tools(self, query: str) -> dict[str, dict[str, Any]]:
        return {name: self.call_tool(name, query) for name in TOOL_NAMES}


def main() -> None:
    config = load_config()
    client = MCPRetrievalClient(
        server_url=config["mcp"]["server_url"],
        timeout_sec=config["mcp"]["timeout_sec"],
    )
    results = client.call_all_tools("nickel production forecast")
    print("MCP server URL:", config["mcp"]["server_url"])
    print("Executed tools:", ", ".join(results.keys()))


if __name__ == "__main__":
    main()
