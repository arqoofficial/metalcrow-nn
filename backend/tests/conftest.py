from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, delete

from app.core.config import settings
from app.core.db import engine, init_db
from app.main import app
from app.models import User
from app.services import parser_client, rate_limit
from tests.utils.parser import FakeParser
from tests.utils.redis import FakeRedisClient
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers


def _cleanup_experiments_data(session: Session) -> None:
    """Remove domain rows left by API tests (ingest/wiki/search/analytics fixtures)."""
    session.execute(
        text(
            """
            TRUNCATE
                experiments.results,
                experiments.experiments,
                experiments.ingest_tasks,
                experiments.documents,
                experiments.entity_aliases,
                experiments.entity_same_as,
                experiments.materials,
                experiments.regimes,
                experiments.labs,
                experiments.researchers,
                experiments.equipment,
                experiments.properties,
                chat_message,
                chat_session
            RESTART IDENTITY CASCADE
            """
        )
    )
    session.execute(text("REFRESH MATERIALIZED VIEW experiments.experiments_flat"))
    session.commit()


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session]:
    with Session(engine) as session:
        init_db(session)
        yield session
        _cleanup_experiments_data(session)
        session.execute(delete(User))
        session.commit()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )


@pytest.fixture
def fake_parser(monkeypatch: pytest.MonkeyPatch) -> FakeParser:
    """Подменяет parser_client in-memory stub'ом — ingest/sources/wiki без parser-main."""
    fake = FakeParser()
    monkeypatch.setattr(parser_client, "upload", fake.upload)
    monkeypatch.setattr(parser_client, "enqueue_process", fake.enqueue_process)
    monkeypatch.setattr(parser_client, "get_status", fake.get_status)
    monkeypatch.setattr(parser_client, "fetch_markdown", fake.fetch_markdown)
    monkeypatch.setattr(parser_client, "fetch_raw", fake.fetch_raw)
    monkeypatch.setattr(parser_client, "get_statistics", fake.get_statistics)
    monkeypatch.setattr(parser_client, "fetch_tree", fake.fetch_tree)
    monkeypatch.setattr(parser_client, "list_raw_files", fake.list_raw_files)
    return fake


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedisClient:
    """Подменяет Redis-клиент rate-limit'а in-memory счётчиком — позволяет
    детерминированно проверить 429 без поднятого Redis."""
    fake = FakeRedisClient()
    monkeypatch.setattr(rate_limit, "_get_client", lambda: fake)
    return fake
