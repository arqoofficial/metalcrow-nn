import json

import httpx
import pytest
import respx

from app.core.config import settings
from app.services import llm

BASE = "https://llm.example.com/v1"


@pytest.fixture(autouse=True)
def _llm_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests want a configured LITSEARCH_BASE_URL; the "unset" tests override
    this back to empty explicitly."""
    monkeypatch.setattr(settings, "LITSEARCH_BASE_URL", BASE)
    monkeypatch.setattr(settings, "LITSEARCH_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LITSEARCH_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(settings, "LITSEARCH_LLM_TIMEOUT", 60)


# --- chat (native tool-calling transport) -----------------------------------


@respx.mock
def test_chat_returns_content_and_ok() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "готовый ответ"}}]},
        )
    )
    result = llm.chat([{"role": "user", "content": "hi"}])
    assert result.ok is True
    assert result.content == "готовый ответ"
    assert result.tool_calls == []


@respx.mock
def test_chat_parses_tool_calls_with_arguments_dict() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "litsearch_search",
                                        "arguments": json.dumps({"query": "nickel"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )
    )
    result = llm.chat([{"role": "user", "content": "hi"}])
    assert result.ok is True
    assert result.content is None
    assert result.tool_calls == [
        {"id": "call_1", "name": "litsearch_search", "arguments": {"query": "nickel"}}
    ]


@respx.mock
def test_chat_sends_tools_tool_choice_and_metadata() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "x"}}]}
        )
    )
    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    llm.chat(
        [{"role": "user", "content": "hi"}],
        tools=tools,
        tool_choice="auto",
        metadata={"session_id": "sess-1"},
    )
    payload = json.loads(route.calls.last.request.content)
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"
    assert payload["metadata"] == {"session_id": "sess-1"}


def test_chat_returns_not_ok_when_base_url_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "LITSEARCH_BASE_URL", "")
    result = llm.chat([{"role": "user", "content": "hi"}])
    assert result.ok is False
    assert result.content is None
    assert result.tool_calls == []


@respx.mock
def test_chat_http_500_returns_not_ok() -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(500))
    result = llm.chat([{"role": "user", "content": "hi"}])
    assert result.ok is False
    assert result.content is None


@respx.mock
def test_chat_malformed_tool_call_arguments_returns_not_ok() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "function": {
                                        "name": "t",
                                        "arguments": "not json {",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )
    )
    result = llm.chat([{"role": "user", "content": "hi"}])
    assert result.ok is False
