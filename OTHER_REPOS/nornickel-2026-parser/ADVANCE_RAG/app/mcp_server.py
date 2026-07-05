"""MCP retrieval-only tool server."""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from app.config.settings import RuntimeConfig, get_settings


class McpToolInput(BaseModel):
    query: str = Field(..., min_length=1)
    source_subfolder: str | None = None
    limit: int | None = Field(default=None, ge=1, le=100)


TOOL_TYPE_MAP = {
    "simple_rag": "dense",
    "sparse_rag": "sparse",
    "advance_rag": "advance",
    "advance_rag_fast": "RRF",
    "grep_rag": "fuzzy",
}


class McpQueryClient:
    def __init__(self, base_url: str, timeout_sec: float = 20.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec

    def query(self, tool_name: str, payload: McpToolInput) -> dict[str, Any]:
        body = {
            "query": payload.query,
            "type": TOOL_TYPE_MAP[tool_name],
            "source_subfolder": payload.source_subfolder,
            "limit": payload.limit,
        }
        body = {k: v for k, v in body.items() if v is not None}
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(f"{self._base_url}/api/v1/query", json=body)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]


def create_mcp_server(runtime: RuntimeConfig, api_base_url: str) -> FastMCP:
    mcp = FastMCP("advance_rag_mcp", host=runtime.mcp.host, port=runtime.mcp.port)
    client = McpQueryClient(api_base_url)

    def _make_tool(tool_name: str):
        def tool_fn(
            query: str,
            source_subfolder: str | None = None,
            limit: int | None = None,
        ) -> dict[str, Any]:
            payload = McpToolInput(
                query=query,
                source_subfolder=source_subfolder,
                limit=limit,
            )
            return client.query(tool_name, payload)

        tool_fn.__name__ = tool_name
        return tool_fn

    for name in TOOL_TYPE_MAP:
        mcp.tool(name=name)(_make_tool(name))

    return mcp


def main() -> None:
    runtime, _ = get_settings()
    default_base = f"http://127.0.0.1:{runtime.api.port}"
    api_base_url = os.getenv("API_BASE_URL", default_base)
    mcp = create_mcp_server(runtime, api_base_url)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
