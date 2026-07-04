from fastapi.testclient import TestClient

from tool_sdk import ToolManifest, create_tool_app, passthrough_invoke


def test_tool_app_health_manifest_invoke() -> None:
    manifest = ToolManifest(
        name="echo",
        description="Echo params back",
        queue="parse.docling",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    app = create_tool_app(
        name="echo",
        version="0.1.0",
        manifest=manifest,
        invoke_handler=passthrough_invoke(lambda params: {"echo": params}),
    )
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "name": "echo", "version": "0.1.0"}

    manifest_resp = client.get("/manifest")
    assert manifest_resp.status_code == 200
    assert manifest_resp.json()["name"] == "echo"

    invoke = client.post(
        "/invoke",
        json={
            "tool": "echo",
            "params": {"message": "hello"},
            "context": {"locale": "ru"},
        },
    )
    assert invoke.status_code == 200
    body = invoke.json()
    assert body["ok"] is True
    assert body["result"] == {"echo": {"message": "hello"}}
