"""LangChain tool wrapper example built from MCP retrieval tools."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from examples.mcp_client_example import TOOL_NAMES, MCPRetrievalClient, load_config


def build_langchain_tools(client: MCPRetrievalClient) -> list[StructuredTool]:
    tools: list[StructuredTool] = []
    for tool_name in TOOL_NAMES:

        def _call(
            query: str,
            source_subfolder: str | None = None,
            limit: int | None = None,
            _name: str = tool_name,
        ) -> dict[str, Any]:
            return client.call_tool(
                _name,
                query=query,
                source_subfolder=source_subfolder,
                limit=limit,
            )

        tools.append(
            StructuredTool.from_function(
                func=_call,
                name=tool_name,
                description=f"MCP bridged retrieval tool: {tool_name}",
            )
        )
    return tools


def main() -> None:
    config = load_config()
    client = MCPRetrievalClient(
        server_url=config["mcp"]["server_url"],
        timeout_sec=config["mcp"]["timeout_sec"],
    )
    tools = build_langchain_tools(client)
    print("Wrap MCP tools for LangChain:", ", ".join(tool.name for tool in tools))
    print("Default subfolder:", config["advancerag"]["default_source_subfolder"])


if __name__ == "__main__":
    main()
