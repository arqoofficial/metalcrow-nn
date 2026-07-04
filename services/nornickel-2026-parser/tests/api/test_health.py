"""Health endpoint tests."""

def test_health_endpoint(api_client) -> None:
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_endpoint(api_client) -> None:
    response = api_client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
