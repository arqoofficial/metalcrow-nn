import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.models import Document


def test_sources_requires_auth(client: TestClient) -> None:
    r = client.get(f"{settings.API_V1_STR}/sources/{uuid.uuid4()}/content")
    assert r.status_code == 401


def test_sources_not_found(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.get(
        f"{settings.API_V1_STR}/sources/{uuid.uuid4()}/content",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 404


def test_sources_download_content_not_in_parser(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
    fake_parser,  # noqa: ARG001
) -> None:
    document = Document(
        parser_path="UPLOAD_DATA/metalcrow/missing/missing.pdf",
        filename="missing.pdf",
        mime_type="application/pdf",
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    r = client.get(
        f"{settings.API_V1_STR}/sources/{document.id}/content",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "File not found in parser storage"


def test_sources_download_content(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
    fake_parser,
) -> None:
    parser_path = "UPLOAD_DATA/metalcrow/test/report.pdf"
    document = Document(
        parser_path=parser_path,
        filename="report.pdf",
        mime_type="application/pdf",
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    fake_parser.uploads[parser_path] = b"%PDF-1.4 report"

    r = client.get(
        f"{settings.API_V1_STR}/sources/{document.id}/content",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 report"
    assert r.headers["content-type"] == "application/pdf"
    assert 'filename="report.pdf"' in r.headers["content-disposition"]
