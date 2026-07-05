import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.schemas.chat import ChatMessageResponse
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import random_email


@pytest.fixture(autouse=True)
def _no_litsearch_in_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTO now offers `literature_search_en` first (spec §2.5); with a reachable
    gateway the model would non-deterministically grab it and pre-empt the
    ontology/KG waterfall these route tests assert. Empty `LITSEARCH_BASE_URL` forces
    the Phase-A loop into the degraded path -> AUTO falls through deterministically."""
    monkeypatch.setattr(settings, "LITSEARCH_BASE_URL", "")


def _create_session(
    client: TestClient,
    headers: dict[str, str],
    title: str | None = "test",
) -> dict[str, Any]:
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions",
        headers=headers,
        json={"title": title},
    )
    assert r.status_code == 200
    body: dict[str, Any] = r.json()
    return body


def _parse_sse(response_text: str) -> ChatMessageResponse:
    assert response_text.startswith("data: ")
    payload = response_text[len("data: ") :].strip()
    return ChatMessageResponse.model_validate(json.loads(payload))


def test_chat_requires_auth(client: TestClient) -> None:
    assert (
        client.post(f"{settings.API_V1_STR}/chat/sessions", json={}).status_code == 401
    )
    assert client.get(f"{settings.API_V1_STR}/chat/sessions").status_code == 401
    assert (
        client.get(f"{settings.API_V1_STR}/chat/sessions/{uuid.uuid4()}").status_code
        == 401
    )
    assert (
        client.post(
            f"{settings.API_V1_STR}/chat/sessions/{uuid.uuid4()}/messages",
            json={"content": "hi"},
        ).status_code
        == 401
    )


def test_create_and_list_sessions(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    created = _create_session(client, normal_user_token_headers, title="my session")
    assert created["title"] == "my session"
    assert "id" in created

    r = client.get(
        f"{settings.API_V1_STR}/chat/sessions", headers=normal_user_token_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert any(s["id"] == created["id"] for s in body["data"])


def test_create_session_reuses_latest_empty_untitled(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    first = _create_session(client, normal_user_token_headers, title=None)
    assert first["title"] is None

    second = _create_session(client, normal_user_token_headers, title=None)
    assert second["id"] == first["id"]

    r = client.get(
        f"{settings.API_V1_STR}/chat/sessions", headers=normal_user_token_headers
    )
    assert r.status_code == 200
    untitled = [s for s in r.json()["data"] if s["title"] is None]
    assert len(untitled) == 1
    assert untitled[0]["id"] == first["id"]


def test_create_session_with_title_creates_new_despite_empty_latest(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    empty = _create_session(client, normal_user_token_headers, title=None)
    named = _create_session(client, normal_user_token_headers, title="Вопрос про Ni")
    assert named["id"] != empty["id"]

    r = client.get(
        f"{settings.API_V1_STR}/chat/sessions", headers=normal_user_token_headers
    )
    ids = {s["id"] for s in r.json()["data"]}
    assert empty["id"] in ids
    assert named["id"] in ids


def test_create_session_new_when_latest_empty_has_messages(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    first = _create_session(client, normal_user_token_headers, title=None)
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions/{first['id']}/messages",
        headers=normal_user_token_headers,
        json={"content": "Привет"},
    )
    assert r.status_code == 200

    second = _create_session(client, normal_user_token_headers, title=None)
    assert second["id"] != first["id"]

    r = client.get(
        f"{settings.API_V1_STR}/chat/sessions", headers=normal_user_token_headers
    )
    ids = {s["id"] for s in r.json()["data"]}
    assert first["id"] in ids
    assert second["id"] in ids


def test_create_session_new_when_latest_is_titled(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    titled = _create_session(client, normal_user_token_headers, title="Старая сессия")
    empty = _create_session(client, normal_user_token_headers, title=None)
    second_empty = _create_session(client, normal_user_token_headers, title=None)
    assert second_empty["id"] == empty["id"]
    assert empty["id"] != titled["id"]


def test_get_session_history_not_found(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.get(
        f"{settings.API_V1_STR}/chat/sessions/{uuid.uuid4()}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 404


def test_get_session_history_not_owned(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    other_headers = authentication_token_from_email(
        client=client, email=random_email(), db=db
    )
    other_session = _create_session(client, other_headers)

    r = client.get(
        f"{settings.API_V1_STR}/chat/sessions/{other_session['id']}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 404


def test_delete_session(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    session = _create_session(client, normal_user_token_headers)

    r = client.delete(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 204

    r = client.get(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 404

    r = client.get(
        f"{settings.API_V1_STR}/chat/sessions", headers=normal_user_token_headers
    )
    assert all(s["id"] != session["id"] for s in r.json()["data"])


def test_delete_session_not_found(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.delete(
        f"{settings.API_V1_STR}/chat/sessions/{uuid.uuid4()}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 404


def test_delete_session_not_owned(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    other_headers = authentication_token_from_email(
        client=client, email=random_email(), db=db
    )
    other_session = _create_session(client, other_headers)

    r = client.delete(
        f"{settings.API_V1_STR}/chat/sessions/{other_session['id']}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 404


def test_delete_session_requires_auth(client: TestClient) -> None:
    r = client.delete(f"{settings.API_V1_STR}/chat/sessions/{uuid.uuid4()}")
    assert r.status_code == 401


def test_post_message_invalid_body(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    session = _create_session(client, normal_user_token_headers)
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}/messages",
        headers=normal_user_token_headers,
        json={"content": ""},
    )
    assert r.status_code == 422


def test_post_message_autotitles_untitled_session(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions",
        headers=normal_user_token_headers,
        json={"title": None},
    )
    assert r.status_code == 200
    session = r.json()
    assert session["title"] is None

    first_message = "Какая твёрдость у стали после закалки?"
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}/messages",
        headers=normal_user_token_headers,
        json={"content": first_message},
    )
    assert r.status_code == 200

    r = client.get(
        f"{settings.API_V1_STR}/chat/sessions", headers=normal_user_token_headers
    )
    updated = next(s for s in r.json()["data"] if s["id"] == session["id"])
    assert updated["title"] == first_message


def test_post_message_autotitles_long_first_message(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions",
        headers=normal_user_token_headers,
        json={"title": None},
    )
    session = r.json()

    long_message = "А" * 100
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}/messages",
        headers=normal_user_token_headers,
        json={"content": long_message},
    )
    assert r.status_code == 200

    r = client.get(
        f"{settings.API_V1_STR}/chat/sessions", headers=normal_user_token_headers
    )
    updated = next(s for s in r.json()["data"] if s["id"] == session["id"])
    assert updated["title"].endswith("…")
    assert len(updated["title"]) == 60


def test_post_message_keeps_existing_title(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    session = _create_session(client, normal_user_token_headers, title="Моя сессия")
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}/messages",
        headers=normal_user_token_headers,
        json={"content": "Совсем другой текст"},
    )
    assert r.status_code == 200

    r = client.get(
        f"{settings.API_V1_STR}/chat/sessions", headers=normal_user_token_headers
    )
    updated = next(s for s in r.json()["data"] if s["id"] == session["id"])
    assert updated["title"] == "Моя сессия"


def test_post_message_happy_path(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    session = _create_session(client, normal_user_token_headers)
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}/messages",
        headers=normal_user_token_headers,
        json={"content": "What do we know about steel hardness?"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    body = _parse_sse(r.text)
    assert body.tools_used == ["hybrid_search"]
    assert body.session_id == uuid.UUID(session["id"])
    assert len(body.claims) == 1

    history = client.get(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}",
        headers=normal_user_token_headers,
    )
    assert history.status_code == 200
    roles = [m["role"] for m in history.json()]
    assert roles == ["user", "assistant"]


def test_post_message_gap_click(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    session = _create_session(client, normal_user_token_headers)
    r = client.post(
        f"{settings.API_V1_STR}/chat/sessions/{session['id']}/messages",
        headers=normal_user_token_headers,
        json={
            "content": "Why is this gap empty?",
            "metadata": {
                "trigger": "gap_click",
                "gap_cell": {
                    "material_id": str(uuid.uuid4()),
                    "material": "Titanium",
                    "property": "Yield strength",
                    "regime_bucket": "high",
                },
            },
        },
    )
    assert r.status_code == 200
    body = _parse_sse(r.text)
    assert body.tools_used == ["generate_hypothesis"]
    assert body.claims[0].kind == "hypothesis"
    assert body.claims[0].gap_cell is not None
    assert body.claims[0].gap_cell.material == "Titanium"
