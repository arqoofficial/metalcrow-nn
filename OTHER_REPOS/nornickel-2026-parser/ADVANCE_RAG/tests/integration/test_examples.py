"""Examples integration tests."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml


def test_example_config_defaults() -> None:
    config_path = Path(__file__).resolve().parents[2] / "examples" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["advancerag"]["default_source_subfolder"] == "01_docling_clean00"
    assert config["advancerag"]["default_limit"] == 10
    assert config["mcp"]["server_url"].startswith("http://")


def test_examples_config_defaults() -> None:
    config_path = Path(__file__).resolve().parents[2] / "examples" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["api"]["base_url"].endswith("/api/v1")


def test_local_tools_mapping() -> None:
    from examples.local_tools import TOOL_TYPE_MAP

    assert TOOL_TYPE_MAP["sparse_rag"] == "sparse"
    assert "sparce_rag" not in TOOL_TYPE_MAP


def test_local_tools_call_rest_api() -> None:
    from examples.local_tools import simple_rag

    with patch("examples.local_tools.httpx.Client") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "query": "test",
            "type": "dense",
            "source_subfolder": "01_docling_clean00",
            "results": [],
        }
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = simple_rag.invoke({"query": "test"})
        assert result["type"] == "dense"


def test_local_tools_are_langchain_tools() -> None:
    from examples.local_tools import advance_rag, advance_rag_fast, grep_rag, simple_rag, sparse_rag

    assert simple_rag.name == "simple_rag"
    assert sparse_rag.name == "sparse_rag"
    assert advance_rag.name == "advance_rag"
    assert advance_rag_fast.name == "advance_rag_fast"
    assert grep_rag.name == "grep_rag"


def test_mcp_client_calls_all_tools() -> None:
    from examples.mcp_client_example import MCPRetrievalClient

    with patch("examples.mcp_client_example.httpx.Client") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        client = MCPRetrievalClient("http://127.0.0.1:8120", timeout_sec=5)
        results = client.call_all_tools("nickel")
        assert set(results.keys()) == {
            "simple_rag",
            "sparse_rag",
            "advance_rag",
            "advance_rag_fast",
            "grep_rag",
        }


def test_langchain_from_mcp_builds_tools() -> None:
    from examples.langchain_from_mcp_example import build_langchain_tools
    from examples.mcp_client_example import MCPRetrievalClient

    with patch.object(MCPRetrievalClient, "call_tool", return_value={"results": []}):
        client = MCPRetrievalClient("http://127.0.0.1:8120", timeout_sec=5)
        tools = build_langchain_tools(client)
        names = {tool.name for tool in tools}
        assert "simple_rag" in names
        assert "sparse_rag" in names
