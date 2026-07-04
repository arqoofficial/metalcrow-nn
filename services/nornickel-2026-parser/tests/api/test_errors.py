"""Step 04 - HTTP error contract tests."""

import pytest


@pytest.mark.parametrize("code", [400, 404, 409, 422, 500])
def test_http_error_code_contract(api_client, code: int) -> None:
    response = api_client.get(f"/api/v1/health/error/{code}")
    assert response.status_code == code


def test_inconsistent_logical_concrete_returns_400(api_client) -> None:
    response = api_client.get(
        "/api/v1/validate/path",
        params={"path": "UPLOAD_DATA/reports/q1.pdf"},
    )
    assert response.status_code == 200

    response = api_client.get(
        "/api/v1/validate/path",
        params={"path": "reports/q1__v01.pdf"},
    )
    assert response.status_code == 400
