import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.routes import ingest as ingest_route
from app.core.config import settings
from app.schemas.ingest import IngestUploadBatchResponse
from tests.utils.parser import FakeParser
from tests.utils.redis import FakeRedisClient

_PDF_CONTENT_TYPE = "application/pdf"
_PPTX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


def _upload_file(
    client: TestClient,
    headers: dict[str, str],
    filename: str = "report.pdf",
    content: bytes = b"%PDF-1.4 body",
) -> object:
    return client.post(
        f"{settings.API_V1_STR}/ingest/upload",
        headers=headers,
        files=[("files", (filename, content, _PDF_CONTENT_TYPE))],
    )


def test_ingest_requires_auth(client: TestClient) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/ingest/upload",
        files=[("files", ("a.pdf", b"%PDF-1.4", _PDF_CONTENT_TYPE))],
    )
    assert r.status_code == 401
    assert client.post(f"{settings.API_V1_STR}/ingest/reindex").status_code == 401
    assert (
        client.get(f"{settings.API_V1_STR}/ingest/status/{uuid.uuid4()}").status_code
        == 401
    )


def test_ingest_requires_superuser(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/ingest/upload",
        headers=normal_user_token_headers,
        files=[("files", ("a.pdf", b"%PDF-1.4", _PDF_CONTENT_TYPE))],
    )
    assert r.status_code == 403
    assert (
        client.post(
            f"{settings.API_V1_STR}/ingest/reindex", headers=normal_user_token_headers
        ).status_code
        == 403
    )


def test_ingest_upload_rejects_bad_mime(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_parser,  # noqa: ARG001 — патчит parser_client как side effect
    fake_redis: FakeRedisClient,  # noqa: ARG001 — патчит Redis-клиент как side effect
) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/ingest/upload",
        headers=superuser_token_headers,
        files=[("files", ("a.txt", b"hello", "text/plain"))],
    )
    assert r.status_code == 415


def test_ingest_upload_rejects_too_large(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_parser,  # noqa: ARG001 — патчит parser_client как side effect
    fake_redis: FakeRedisClient,  # noqa: ARG001 — патчит Redis-клиент как side effect
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingest_route, "_MAX_FILE_SIZE_BYTES", 4)
    r = client.post(
        f"{settings.API_V1_STR}/ingest/upload",
        headers=superuser_token_headers,
        files=[("files", ("a.pdf", b"%PDF-1.4 more bytes", _PDF_CONTENT_TYPE))],
    )
    assert r.status_code == 413


def test_ingest_upload_single_file_field(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_parser,
    fake_redis: FakeRedisClient,  # noqa: ARG001
) -> None:
    """Legacy/single field name `file` must still work."""
    r = client.post(
        f"{settings.API_V1_STR}/ingest/upload",
        headers=superuser_token_headers,
        files=[("file", ("legacy.pdf", b"%PDF-1.4 legacy", _PDF_CONTENT_TYPE))],
    )
    assert r.status_code == 201
    body = IngestUploadBatchResponse.model_validate(r.json())
    assert body.count == 1


def test_ingest_upload_accepts_pptx(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_parser,
    fake_redis: FakeRedisClient,  # noqa: ARG001
) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/ingest/upload",
        headers=superuser_token_headers,
        files=[("files", ("slides.pptx", b"PK\x03\x04 pptx", _PPTX_CONTENT_TYPE))],
    )
    assert r.status_code == 201
    body = IngestUploadBatchResponse.model_validate(r.json())
    assert body.count == 1
    assert len(fake_parser.uploads) == 1


def test_ingest_upload_happy_path_l0_only(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_parser,
    fake_redis: FakeRedisClient,  # noqa: ARG001 — патчит Redis-клиент как side effect
) -> None:
    r = _upload_file(client, superuser_token_headers)
    assert r.status_code == 201
    uploaded = IngestUploadBatchResponse.model_validate(r.json())
    assert uploaded.count == 1
    # Синхронно после upload документ ещё L0 — воркер меняет уровень асинхронно.
    assert uploaded.data[0].processing_level == "L0"
    assert len(fake_parser.uploads) == 1


def test_ingest_upload_enqueues_l1_processing(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_parser,  # noqa: ARG001 — патчит parser_client как side effect
    fake_redis: FakeRedisClient,  # noqa: ARG001 — патчит Redis-клиент как side effect
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upload через интерфейс должен сразу поставить L1-парсинг в очередь."""
    from app.services import tasks as tasks_module

    captured: list[tuple[uuid.UUID, list[uuid.UUID], str]] = []
    monkeypatch.setattr(
        tasks_module,
        "enqueue_run",
        lambda task_id, document_ids, level: captured.append(
            (task_id, document_ids, level)
        ),
    )

    r = _upload_file(client, superuser_token_headers)
    assert r.status_code == 201
    uploaded = IngestUploadBatchResponse.model_validate(r.json())
    assert uploaded.task_id is not None
    assert uploaded.data[0].latest_task_status == "queued"
    assert uploaded.data[0].latest_task_progress == 0.0

    assert len(captured) == 1
    task_id, document_ids, level = captured[0]
    assert task_id == uploaded.task_id
    assert document_ids == [uploaded.data[0].id]
    assert level == "L1"


def test_ingest_upload_multi_file(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_parser,
    fake_redis: FakeRedisClient,  # noqa: ARG001
) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/ingest/upload",
        headers=superuser_token_headers,
        files=[
            ("files", ("a.pdf", b"%PDF-1.4 a", _PDF_CONTENT_TYPE)),
            ("files", ("b.pdf", b"%PDF-1.4 b", _PDF_CONTENT_TYPE)),
            ("files", ("c.pdf", b"%PDF-1.4 c", _PDF_CONTENT_TYPE)),
        ],
    )
    assert r.status_code == 201
    body = IngestUploadBatchResponse.model_validate(r.json())
    assert body.count == 3
    assert len(body.data) == 3
    assert len(fake_parser.uploads) == 3


def test_ingest_delete_file(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_parser,
    fake_redis: FakeRedisClient,  # noqa: ARG001
) -> None:
    upload = _upload_file(client, superuser_token_headers)
    doc_id = upload.json()["data"][0]["id"]

    r = client.delete(
        f"{settings.API_V1_STR}/ingest/files/{doc_id}",
        headers=superuser_token_headers,
    )
    assert r.status_code == 204

    files = client.get(
        f"{settings.API_V1_STR}/ingest/files",
        headers=superuser_token_headers,
    )
    ids = [item["id"] for item in files.json()["data"]]
    assert doc_id not in ids


def test_ingest_delete_not_found(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.delete(
        f"{settings.API_V1_STR}/ingest/files/{uuid.uuid4()}",
        headers=superuser_token_headers,
    )
    assert r.status_code == 404


def test_ingest_status_not_found(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.get(
        f"{settings.API_V1_STR}/ingest/status/{uuid.uuid4()}",
        headers=superuser_token_headers,
    )
    assert r.status_code == 404


def test_ingest_reindex_not_implemented(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/ingest/reindex", headers=superuser_token_headers
    )
    assert r.status_code == 501


def test_ingest_upload_rate_limit(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_parser,  # noqa: ARG001 — патчит parser_client как side effect
    fake_redis: FakeRedisClient,  # noqa: ARG001 — патчит Redis-клиент как side effect
) -> None:
    responses = [
        _upload_file(client, superuser_token_headers, filename=f"doc-{i}.pdf")
        for i in range(ingest_route._RATE_LIMIT_PER_MINUTE + 1)
    ]
    assert [r.status_code for r in responses[:-1]] == [
        201
    ] * ingest_route._RATE_LIMIT_PER_MINUTE
    assert responses[-1].status_code == 429


def test_ingest_files_and_admin_coverage(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_parser,  # noqa: ARG001
    fake_redis: FakeRedisClient,  # noqa: ARG001
) -> None:
    upload = _upload_file(client, superuser_token_headers)
    assert upload.status_code == 201

    files = client.get(
        f"{settings.API_V1_STR}/ingest/files",
        headers=superuser_token_headers,
    )
    assert files.status_code == 200
    body = files.json()
    assert body["count"] >= 1
    assert body["data"][0]["processing_level"] == "L0"

    doc_id = body["data"][0]["id"]
    detail = client.get(
        f"{settings.API_V1_STR}/ingest/files/{doc_id}",
        headers=superuser_token_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["document"]["id"] == doc_id

    coverage = client.get(
        f"{settings.API_V1_STR}/admin/coverage",
        headers=superuser_token_headers,
    )
    assert coverage.status_code == 200
    coverage_body = coverage.json()
    assert coverage_body["total_files"] >= 1
    by_level = {item["level"]: item["count"] for item in coverage_body["by_level"]}
    assert by_level["L0"] == coverage_body["total_files"]
    assert by_level["L1"] == 0


def test_ingest_run_requires_documents(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/ingest/run",
        headers=superuser_token_headers,
        json={"document_ids": [], "level": "L1"},
    )
    assert r.status_code == 400


def test_enqueue_uses_v5_parse_task(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import tasks as tasks_module

    captured: list[str] = []

    class FakeSignature:
        def __init__(self, name: str, args: list) -> None:  # noqa: ARG002
            captured.append(name)

        def apply_async(self) -> None:
            return None

        def __or__(self, other: "FakeSignature") -> "FakeSignature":
            return other

    monkeypatch.setattr(tasks_module.celery_app, "signature", FakeSignature)
    tasks_module.enqueue_ingest_pipeline(uuid.uuid4(), [uuid.uuid4()])
    assert captured == [tasks_module.V5_PARSE_TASK]


def test_admin_raw_files_lists_unparsed_only(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_parser: FakeParser,
) -> None:
    fake_parser.uploads["RAW_DATA/reports/pending.pdf"] = b"%PDF pending"
    fake_parser.uploads["RAW_DATA/reports/done.pdf"] = b"%PDF done"
    fake_parser.stage0_done_paths.add("RAW_DATA/reports/done.pdf")

    response = client.get(
        f"{settings.API_V1_STR}/admin/raw-files",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["data"][0]["path"] == "RAW_DATA/reports/pending.pdf"


def test_admin_parse_raw_file_enqueues_l1(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_parser: FakeParser,
    fake_redis: FakeRedisClient,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import tasks as tasks_module

    path = "RAW_DATA/Доклады/report.pdf"
    fake_parser.uploads[path] = b"%PDF report"

    captured: list[tuple[uuid.UUID, list[uuid.UUID], str]] = []

    def fake_enqueue(
        task_id: uuid.UUID, document_ids: list[uuid.UUID], level: str
    ) -> None:
        captured.append((task_id, document_ids, level))

    monkeypatch.setattr(tasks_module, "enqueue_run", fake_enqueue)

    response = client.post(
        f"{settings.API_V1_STR}/admin/raw-files/parse",
        headers=superuser_token_headers,
        json={"path": path},
    )
    assert response.status_code == 202
    assert len(captured) == 1
    assert captured[0][2] == "L1"

    files = client.get(
        f"{settings.API_V1_STR}/ingest/files",
        headers=superuser_token_headers,
    )
    assert files.status_code == 200
    assert any(item["filename"] == "report.pdf" for item in files.json()["data"])
