from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from app.services import ontology_client


def _mock_response(
    *, status_code: int = 200, json_data: Any = None, raise_error: bool = False
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    if raise_error:
        resp.raise_for_status.side_effect = httpx.HTTPError("boom")
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_available_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _mock_response(status_code=200)
    )
    assert ontology_client.available() is True


def test_available_false_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> None:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", _boom)
    assert ontology_client.available() is False


def test_ask_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"question": "q", "tools_used": ["evidence"], "claims": []}
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _mock_response(json_data=payload),
    )
    assert ontology_client.ask("q") == payload


def test_ask_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _mock_response(raise_error=True),
    )
    assert ontology_client.ask("q") is None


def test_invoke_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _mock_response(json_data={"ok": True, "result": {"n": 1}}),
    )
    assert ontology_client.invoke("find_gaps") == {"n": 1}


def test_invoke_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _mock_response(json_data={"ok": False, "error": "nope"}),
    )
    assert ontology_client.invoke("find_gaps") is None


def test_evidence_builds_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _post(*_a: object, **kwargs: object) -> MagicMock:
        captured.update(kwargs.get("json", {}))  # type: ignore[arg-type]
        return _mock_response(json_data={"ok": True, "result": {"answer": "ok"}})

    monkeypatch.setattr(httpx, "post", _post)
    result = ontology_client.evidence(process="leaching", quantity_kind="recovery")
    assert result == {"answer": "ok"}
    assert captured == {
        "tool": "evidence",
        "args": {"process": "leaching", "quantity_kind": "recovery"},
    }


def test_invoke_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> None:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", _boom)
    assert ontology_client.invoke("find_gaps") is None


def test_helper_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ontology_client,
        "invoke",
        lambda tool, args=None: {"tool": tool, "args": args or {}},
    )
    assert ontology_client.find_gaps(2) == {
        "tool": "find_gaps",
        "args": {"min_sources": 2},
    }
    assert ontology_client.find_contradictions() == {
        "tool": "find_contradictions",
        "args": {},
    }
    assert ontology_client.compare_practice("x") == {
        "tool": "compare_practice",
        "args": {"process": "x"},
    }
    assert ontology_client.find_experts_by_topic("Ni", 3) == {
        "tool": "find_experts_by_topic",
        "args": {"topic": "Ni", "limit": 3},
    }
    assert ontology_client.get_subgraph("mat:1", 2) == {
        "tool": "get_subgraph",
        "args": {"entity": "mat:1", "depth": 2},
    }
    assert ontology_client.evidence_profile("hardness", material="steel") == {
        "tool": "evidence_profile",
        "args": {"quantity_kind": "hardness", "material": "steel"},
    }
