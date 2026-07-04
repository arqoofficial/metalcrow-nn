import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.deps import SessionDep, get_current_user
from app.models import Document
from app.services import parser_client

router = APIRouter(
    prefix="/sources", tags=["sources"], dependencies=[Depends(get_current_user)]
)

_STREAM_CHUNK_SIZE = 32 * 1024


@router.get("/{doc_id}/content")
def download_content(session: SessionDep, doc_id: uuid.UUID) -> StreamingResponse:
    """Stream raw document bytes from parser SHARED through the API."""
    document = session.get(Document, doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        raw = parser_client.fetch_raw(document.parser_path)
    except parser_client.ParserError:
        raise HTTPException(
            status_code=404,
            detail="File not found in parser storage",
        ) from None

    def iterfile() -> Iterator[bytes]:
        yield raw.data

    safe_filename = document.filename.replace('"', "")
    return StreamingResponse(
        iterfile(),
        media_type=document.mime_type or raw.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
        },
    )
