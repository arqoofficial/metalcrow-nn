"""Simple agent usage example with local REST tools."""

from __future__ import annotations

from examples.local_tools import advance_rag, grep_rag, load_config, simple_rag


def main() -> None:
    config = load_config()
    print("REST base URL:", config["api"]["base_url"])
    tools = [simple_rag, advance_rag, grep_rag]
    prompt = "nickel production forecast"
    result = tools[0].invoke({"query": prompt})
    print("Tool count:", len(tools))
    print("First tool response keys:", sorted(result.keys()))


if __name__ == "__main__":
    main()
