import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(mock_redis, mock_s3):
    """Patch Redis and StorageClient.presign_url for a `done` job with an object_key."""
    job = {
        "job_id": "j1",
        "status": "done",
        "object_key": "j1.pdf",
    }
    mock_redis.get.return_value = json.dumps(job)
    mock_s3.presign_url.return_value = "http://x"

    with (
        patch("app.main.redis_client", mock_redis),
        patch("app.main.storage", mock_s3),
    ):
        from app.main import app

        yield TestClient(app)


def test_get_job_returns_object_key_when_done(client):
    resp = client.get("/jobs/j1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object_key"] == "j1.pdf"
    assert data["status"] == "done"
    assert data["url"] == "http://x"
