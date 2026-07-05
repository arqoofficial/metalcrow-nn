import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.config import settings

BASE = "https://llm.example.com/v1"


@pytest.fixture(autouse=True)
def _cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LITSEARCH_BASE_URL", BASE)
    monkeypatch.setattr(settings, "LITSEARCH_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LITSEARCH_LLM_MODEL", "deepseek/deepseek-v4-flash__or")


@respx.mock
def test_llm_health_ok(client: TestClient, superuser_token_headers: dict[str, str]) -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})
    )
    r = client.get(
        f"{settings.API_V1_STR}/utils/llm-health", headers=superuser_token_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["model"] == "deepseek/deepseek-v4-flash__or"


@respx.mock
def test_llm_health_reports_unreachable(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(502))
    r = client.get(
        f"{settings.API_V1_STR}/utils/llm-health", headers=superuser_token_headers
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_llm_health_unreachable_when_base_url_unset(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "LITSEARCH_BASE_URL", "")
    r = client.get(
        f"{settings.API_V1_STR}/utils/llm-health", headers=superuser_token_headers
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_llm_health_requires_auth(client: TestClient) -> None:
    """M4: unauthenticated must not be able to trigger a real (paid) gateway
    round-trip — the route is superuser-gated, same convention as the other
    admin/diagnostic routes in this module (e.g. `/utils/test-email/`)."""
    r = client.get(f"{settings.API_V1_STR}/utils/llm-health")
    assert r.status_code == 401
