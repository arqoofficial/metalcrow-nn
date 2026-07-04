import json
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def mock_deps(mock_redis, mock_s3):
    """Patch Redis and StorageClient for all route tests."""
    with (
        patch("app.main.redis_client", mock_redis),
        patch("app.main.storage", mock_s3),
        patch("app.main.fetch_article", return_value=b"%PDF"),
    ):
        yield mock_redis, mock_s3


@pytest.fixture
def client(mock_deps):
    from app.main import app
    return TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_post_fetch_returns_job_id(client, mock_deps):
    mock_redis, _ = mock_deps
    mock_redis.set.return_value = True

    resp = client.post("/fetch", json={"doi": "10.1234/test"})
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "pending"
    mock_redis.set.assert_called_once()


def test_get_job_pending(client, mock_deps):
    mock_redis, _ = mock_deps
    job = {
        "job_id": "abc123",
        "doi": "10.1234/test",
        "status": "pending",
        "object_key": None,
        "error": None,
        "created_at": "2026-03-22T10:00:00Z",
    }
    mock_redis.get.return_value = json.dumps(job)

    resp = client.get("/jobs/abc123")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert data["url"] is None


def test_get_job_done_returns_presigned_url(client, mock_deps):
    mock_redis, mock_s3 = mock_deps
    job = {
        "job_id": "abc123",
        "doi": "10.1234/test",
        "status": "done",
        "object_key": "abc123.pdf",
        "error": None,
        "created_at": "2026-03-22T10:00:00Z",
    }
    mock_redis.get.return_value = json.dumps(job)
    mock_s3.presign_url.return_value = "http://localhost:9092/articles/abc123.pdf?sig=x"

    resp = client.get("/jobs/abc123")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done"
    assert "abc123.pdf" in data["url"]


def test_get_job_failed(client, mock_deps):
    mock_redis, _ = mock_deps
    job = {
        "job_id": "abc123",
        "doi": "10.1234/test",
        "status": "failed",
        "object_key": None,
        "error": "Article not found",
        "created_at": "2026-03-22T10:00:00Z",
    }
    mock_redis.get.return_value = json.dumps(job)

    resp = client.get("/jobs/abc123")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert data["error"] == "Article not found"


def test_get_job_not_found(client, mock_deps):
    mock_redis, _ = mock_deps
    mock_redis.get.return_value = None

    resp = client.get("/jobs/doesnotexist")
    assert resp.status_code == 404


def test_run_fetch_fires_webhook_on_done(mock_redis, mock_s3):
    """When ARTICLE_PROCESSOR_WEBHOOK_URL is set and conversation_id provided, webhook is POSTed."""
    mock_redis.get.return_value = '{"job_id":"j1","doi":"10.1/x","status":"running","object_key":null,"error":null,"created_at":"2026-01-01T00:00:00Z"}'
    mock_redis.set.return_value = True

    with (
        patch("app.main.redis_client", mock_redis),
        patch("app.main.storage", mock_s3),
        patch("app.main.fetch_article", return_value=b"%PDF"),
        patch("app.main.settings") as mock_settings,
        patch("app.main.requests") as mock_requests,
    ):
        mock_settings.article_processor_webhook_url = "http://processor/ingest"
        mock_s3.upload_pdf.return_value = None
        mock_s3.presign_url.return_value = "http://minio/j1.pdf"

        from app.main import _run_fetch
        _run_fetch("j1", "10.1/x", conversation_id="conv-abc")

        mock_requests.post.assert_called_once()
        call_kwargs = mock_requests.post.call_args
        assert call_kwargs[0][0] == "http://processor/ingest"
        payload = call_kwargs[1]["json"]
        assert payload["conversation_id"] == "conv-abc"
        assert payload["doi"] == "10.1/x"
        assert payload["job_id"] == "j1"


def test_run_fetch_skips_webhook_when_no_conversation_id(mock_redis, mock_s3):
    """When conversation_id is not provided, webhook is skipped even if URL is set."""
    mock_redis.get.return_value = '{"job_id":"j1","doi":"10.1/x","status":"running","object_key":null,"error":null,"created_at":"2026-01-01T00:00:00Z"}'
    mock_redis.set.return_value = True

    with (
        patch("app.main.redis_client", mock_redis),
        patch("app.main.storage", mock_s3),
        patch("app.main.fetch_article", return_value=b"%PDF"),
        patch("app.main.settings") as mock_settings,
        patch("app.main.requests") as mock_requests,
    ):
        mock_settings.article_processor_webhook_url = "http://processor/ingest"
        mock_s3.upload_pdf.return_value = None

        from app.main import _run_fetch
        _run_fetch("j1", "10.1/x")  # no conversation_id

        mock_requests.post.assert_not_called()


def test_run_fetch_skips_webhook_when_url_empty(mock_redis, mock_s3):
    """When ARTICLE_PROCESSOR_WEBHOOK_URL is empty, no POST is made."""
    mock_redis.get.return_value = '{"job_id":"j2","doi":"10.1/y","status":"running","object_key":null,"error":null,"created_at":"2026-01-01T00:00:00Z"}'
    mock_redis.set.return_value = True

    with (
        patch("app.main.redis_client", mock_redis),
        patch("app.main.storage", mock_s3),
        patch("app.main.fetch_article", return_value=b"%PDF"),
        patch("app.main.settings") as mock_settings,
        patch("app.main.requests") as mock_requests,
    ):
        mock_settings.article_processor_webhook_url = ""
        mock_s3.upload_pdf.return_value = None

        from app.main import _run_fetch
        _run_fetch("j2", "10.1/y")

        mock_requests.post.assert_not_called()


def test_run_fetch_webhook_failure_does_not_raise(mock_redis, mock_s3):
    """A webhook POST failure must not propagate — job remains done."""
    mock_redis.get.return_value = '{"job_id":"j3","doi":"10.1/z","status":"running","object_key":null,"error":null,"created_at":"2026-01-01T00:00:00Z"}'
    mock_redis.set.return_value = True

    with (
        patch("app.main.redis_client", mock_redis),
        patch("app.main.storage", mock_s3),
        patch("app.main.fetch_article", return_value=b"%PDF"),
        patch("app.main.settings") as mock_settings,
        patch("app.main.requests") as mock_requests,
    ):
        mock_settings.article_processor_webhook_url = "http://processor/ingest"
        mock_s3.upload_pdf.return_value = None
        mock_requests.post.side_effect = Exception("connection refused")

        from app.main import _run_fetch
        _run_fetch("j3", "10.1/z")  # must not raise

        # Job should still be marked done
        set_calls = mock_redis.set.call_args_list
        last_job = json.loads(set_calls[-1][0][1])
        assert last_job["status"] == "done"
