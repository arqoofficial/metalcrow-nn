from fastapi.testclient import TestClient

from app.core.config import settings
from app.services import parser_client


def _sample_tree() -> parser_client.FileTreeNode:
    stage = parser_client.FileTreeNode(
        name="01_docling_clean00",
        type="dir",
        children=[
            parser_client.FileTreeNode(
                name="UPLOAD_DATA",
                type="dir",
                children=[
                    parser_client.FileTreeNode(
                        name="metalcrow",
                        type="dir",
                        children=[
                            parser_client.FileTreeNode(
                                name="paper.pdf.md",
                                type="file",
                                children=[],
                            )
                        ],
                    )
                ],
            )
        ],
    )
    return parser_client.FileTreeNode(name="SHARED", type="dir", children=[stage])


def test_wiki_requires_auth(client: TestClient) -> None:
    assert (
        client.get(f"{settings.API_V1_STR}/wiki/search", params={"q": "paper"}).status_code
        == 401
    )
    assert client.get(f"{settings.API_V1_STR}/wiki/tree").status_code == 401


def test_wiki_tree(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    fake_parser,
) -> None:
    fake_parser.tree = _sample_tree()

    r = client.get(
        f"{settings.API_V1_STR}/wiki/tree",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["resolved_root"] == "01_docling_clean00"
    assert body["children"][0]["name"] == "UPLOAD_DATA"
    assert body["children"][0]["children"][0]["children"][0]["path"] == (
        "01_docling_clean00/UPLOAD_DATA/metalcrow/paper.pdf.md"
    )


def test_wiki_search(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    fake_parser,
) -> None:
    fake_parser.tree = _sample_tree()

    r = client.get(
        f"{settings.API_V1_STR}/wiki/search",
        headers=normal_user_token_headers,
        params={"q": "paper"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["results"][0]["title"] == "paper.pdf.md"


def test_wiki_document_content(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    fake_parser,
) -> None:
    okf_path = "01_docling_clean00/UPLOAD_DATA/metalcrow/paper.pdf.md"
    fake_parser.markdowns[okf_path] = "Extracted paragraph."

    r = client.get(
        f"{settings.API_V1_STR}/wiki/documents/content",
        headers=normal_user_token_headers,
        params={"okf_path": okf_path},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["markdown"] == "Extracted paragraph."
    assert body["raw_path"] == "UPLOAD_DATA/metalcrow/paper.pdf"
    assert body["display_path"] == "UPLOAD_DATA/metalcrow/paper.pdf.md"


def test_wiki_document_content_not_found(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    fake_parser,  # noqa: ARG001
) -> None:
    r = client.get(
        f"{settings.API_V1_STR}/wiki/documents/content",
        headers=normal_user_token_headers,
        params={"okf_path": "01_docling_clean00/UPLOAD_DATA/missing.pdf.md"},
    )
    assert r.status_code == 404


def test_wiki_download_markdown(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    fake_parser,
) -> None:
    okf_path = "01_docling_clean00/UPLOAD_DATA/metalcrow/paper.pdf.md"
    fake_parser.markdowns[okf_path] = "# Title\n\nBody"

    r = client.get(
        f"{settings.API_V1_STR}/wiki/documents/download/markdown",
        headers=normal_user_token_headers,
        params={"okf_path": okf_path},
    )
    assert r.status_code == 200
    assert "Title" in r.text
    assert "attachment" in r.headers.get("content-disposition", "")


def test_wiki_download_raw(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    fake_parser,
) -> None:
    okf_path = "01_docling_clean00/UPLOAD_DATA/metalcrow/paper.pdf.md"
    fake_parser.uploads["UPLOAD_DATA/metalcrow/paper.pdf"] = b"%PDF-1.4"

    r = client.get(
        f"{settings.API_V1_STR}/wiki/documents/download/raw",
        headers=normal_user_token_headers,
        params={"okf_path": okf_path},
    )
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4"


def test_wiki_download_markdown_with_unicode_filename(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    fake_parser,
) -> None:
    okf_path = (
        "01_docling_clean00/RAW_DATA/Материалы конференции/"
        "Copyright_letter_Письмо об авторском праве (на материалы презентации).pdf.md"
    )
    fake_parser.markdowns[okf_path] = "# Письмо"

    r = client.get(
        f"{settings.API_V1_STR}/wiki/documents/download/markdown",
        headers=normal_user_token_headers,
        params={"okf_path": okf_path},
    )
    assert r.status_code == 200
    assert "Письмо" in r.text
    assert "filename*=UTF-8''" in r.headers.get("content-disposition", "")


def test_wiki_download_raw_with_unicode_filename(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    fake_parser,
) -> None:
    okf_path = (
        "01_docling_clean00/RAW_DATA/Материалы конференции/"
        "Copyright_letter_Письмо об авторском праве (на материалы презентации).pdf.md"
    )
    raw_path = (
        "RAW_DATA/Материалы конференции/"
        "Copyright_letter_Письмо об авторском праве (на материалы презентации).pdf"
    )
    fake_parser.uploads[raw_path] = b"%PDF-1.4"

    r = client.get(
        f"{settings.API_V1_STR}/wiki/documents/download/raw",
        headers=normal_user_token_headers,
        params={"okf_path": okf_path},
    )
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4"
    assert "filename*=UTF-8''" in r.headers.get("content-disposition", "")
