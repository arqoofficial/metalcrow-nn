from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user
from app.http_headers.disposition import attachment_content_disposition
from app.schemas.wiki import (
    WIKI_STAGE_ROOT,
    WikiDocumentContent,
    WikiSearchResponse,
    WikiTreeResponse,
)
from app.services import parser_client, wiki as wiki_service

router = APIRouter(
    prefix="/wiki", tags=["wiki"], dependencies=[Depends(get_current_user)]
)

_STREAM_CHUNK_SIZE = 32 * 1024


@router.get("/tree", response_model=WikiTreeResponse)
def get_tree(
    root: str = Query(
        WIKI_STAGE_ROOT,
        description="Subtree relative to SHARED/, default is cleaned Docling markdown.",
    ),
    max_depth: int = Query(10, ge=0, le=10),
) -> WikiTreeResponse:
    """GET /api/v1/wiki/tree — processed markdown folder tree from parser SHARED/."""
    try:
        return wiki_service.get_tree(root=root, max_depth=max_depth)
    except parser_client.ParserError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/search", response_model=WikiSearchResponse)
def search(q: str = Query(..., min_length=1)) -> WikiSearchResponse:
    """GET /api/v1/wiki/search — find processed markdown files by filename/path."""
    try:
        return wiki_service.search_documents(q)
    except parser_client.ParserError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/documents/content", response_model=WikiDocumentContent)
def get_document_content(
    okf_path: str = Query(..., description="OKF path under SHARED/, e.g. 01_docling_clean00/UPLOAD_DATA/reports/q1.pdf.md"),
) -> WikiDocumentContent:
    """GET /api/v1/wiki/documents/content — read cleaned markdown for display."""
    try:
        content = wiki_service.get_document_content(okf_path)
    except parser_client.ParserError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if content is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return content


@router.get("/documents/download/markdown")
def download_markdown(
    okf_path: str = Query(..., description="OKF path under SHARED/."),
) -> StreamingResponse:
    """Download cleaned markdown file."""
    try:
        filename, markdown = wiki_service.fetch_document_markdown_download(okf_path)
    except parser_client.ParserError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    payload = markdown.encode("utf-8")

    def iterfile() -> Iterator[bytes]:
        yield payload

    return StreamingResponse(
        iterfile(),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": attachment_content_disposition(filename),
        },
    )


@router.get("/documents/download/raw")
def download_raw(
    okf_path: str = Query(..., description="OKF path under SHARED/."),
) -> StreamingResponse:
    """Download the original raw document for a processed markdown file."""
    try:
        raw = wiki_service.fetch_document_raw_download(okf_path)
    except parser_client.ParserError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    def iterfile() -> Iterator[bytes]:
        for offset in range(0, len(raw.data), _STREAM_CHUNK_SIZE):
            yield raw.data[offset : offset + _STREAM_CHUNK_SIZE]

    return StreamingResponse(
        iterfile(),
        media_type=raw.content_type,
        headers={
            "Content-Disposition": attachment_content_disposition(raw.filename),
        },
    )
