"""MCP retrieval tool integration tests."""

from unittest.mock import MagicMock, patch

from app.mcp_server import TOOL_TYPE_MAP, create_mcp_server


def test_tool_type_mappings() -> None:
    assert TOOL_TYPE_MAP["simple_rag"] == "dense"
    assert TOOL_TYPE_MAP["sparse_rag"] == "sparse"
    assert TOOL_TYPE_MAP["advance_rag"] == "advance"
    assert TOOL_TYPE_MAP["advance_rag_fast"] == "RRF"
    assert TOOL_TYPE_MAP["grep_rag"] == "fuzzy"


def test_indexing_tools_not_exposed(test_app) -> None:
    app, _, _, _ = test_app
    runtime = app.state.app_state.runtime
    mcp = create_mcp_server(runtime, "http://127.0.0.1:8114")
    tool_names = {tool.name for tool in mcp._tool_manager._tools.values()}  # type: ignore[attr-defined]
    assert "index_doc" not in tool_names
    assert "index_path" not in tool_names
    assert "sparse_rag" in tool_names
    assert "sparce_rag" not in tool_names


def test_mcp_client_calls_query_api(test_app) -> None:
    from app.mcp_server import McpQueryClient, McpToolInput

    client = McpQueryClient("http://127.0.0.1:8114")
    with patch("httpx.Client") as mock_client_cls:
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

        result = client.query("simple_rag", McpToolInput(query="test"))
        assert result["type"] == "dense"
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[1]["json"]["type"] == "dense"
