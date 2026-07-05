"""MCP server entrypoint tests."""

from types import SimpleNamespace

from app.mcp_server import main


def test_main_creates_and_runs_server(monkeypatch) -> None:
    runtime = SimpleNamespace(
        api=SimpleNamespace(port=8114),
        mcp=SimpleNamespace(host="0.0.0.0", port=8120),
    )
    called: dict[str, object] = {}

    class DummyMcp:
        def run(self) -> None:
            called["ran"] = True

    monkeypatch.setattr("app.mcp_server.get_settings", lambda: (runtime, None))

    def fake_create(runtime_obj, api_base_url):
        called["runtime"] = runtime_obj
        called["api_base_url"] = api_base_url
        return DummyMcp()

    monkeypatch.setattr("app.mcp_server.create_mcp_server", fake_create)
    main()
    assert called["runtime"] is runtime
    assert called["api_base_url"] == "http://127.0.0.1:8114"
    assert called["ran"] is True
