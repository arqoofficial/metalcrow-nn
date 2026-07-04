import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlmodel import select

from app.api.deps import SessionDep, get_current_active_superuser
from app.models import Document, IngestStatus, IngestTask, ProcessingLevel, User
from app.schemas.ingest import (
    AdminCoverageResponse,
    DocumentFileSummary,
    IngestFileDetailResponse,
    IngestFilesResponse,
    IngestRunRequest,
    IngestUploadBatchResponse,
    IngestUploadResponse,
    ParseRawDataFileRequest,
    RawDataFilesResponse,
)
from app.services import ingest as ingest_service
from app.services import parser_client, rate_limit, tasks

router = APIRouter(
    prefix="/ingest",
    tags=["ingest"],
    dependencies=[Depends(get_current_active_superuser)],
)

admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_active_superuser)],
)

# SPEC_V3 §8.5 / SPEC_V5 §9: MIME whitelist, 50MB, 10 uploads/минуту
_MIME_WHITELIST = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
}
_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024
_RATE_LIMIT_PER_MINUTE = 10
_MAX_FILES_PER_REQUEST = 20


def _to_response(task: IngestTask) -> IngestUploadResponse:
    return IngestUploadResponse(
        task_id=task.id,
        status=task.status,
        progress=task.progress,
        stage_name=task.stage_name,
        error=task.error,
    )


def _to_summary(document: Document) -> DocumentFileSummary:
    return DocumentFileSummary(
        id=document.id,
        filename=document.filename,
        mime_type=document.mime_type,
        processing_level=document.processing_level,
        okf_raw_path=document.okf_raw_path,
        uploaded_at=document.uploaded_at,
    )


def _validate_file(file: UploadFile, data: bytes) -> None:
    if file.content_type not in _MIME_WHITELIST:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"MIME type '{file.content_type}' is not allowed",
        )
    if len(data) > _MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File '{file.filename or 'document'}' exceeds the 50MB limit",
        )


@router.post(
    "/upload",
    response_model=IngestUploadBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload(
    session: SessionDep,
    current_user: Annotated[User, Depends(get_current_active_superuser)],
    file: Annotated[UploadFile | None, File()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> IngestUploadBatchResponse:
    """POST /api/v1/ingest/upload — parser SHARED + Document L0, сразу ставит L1-парсинг в очередь."""
    incoming: list[UploadFile] = list(files or [])
    if file is not None:
        incoming.append(file)
    if not incoming:
        raise HTTPException(status_code=400, detail="At least one file is required")
    if len(incoming) > _MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"At most {_MAX_FILES_PER_REQUEST} files per request",
        )

    for _ in incoming:
        rate_limit.check_rate_limit(
            f"ingest_upload:{current_user.id}",
            limit=_RATE_LIMIT_PER_MINUTE,
            window_seconds=60,
        )

    created_documents: list[Document] = []
    for upload_file in incoming:
        data = upload_file.file.read()
        _validate_file(upload_file, data)

        doc_id = uuid.uuid4()
        filename = upload_file.filename or "document"
        logical_path = f"metalcrow/{doc_id}/{filename}"
        try:
            uploaded = parser_client.upload(logical_path, filename, data)
        except parser_client.ParserError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Parser upload failed: {exc}",
            ) from exc

        document = Document(
            id=doc_id,
            parser_path=uploaded.resolved_path,
            filename=filename,
            mime_type=upload_file.content_type,
            processing_level=ProcessingLevel.L0,
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        created_documents.append(document)

    # Upload сразу ставит L1-парсинг в очередь (Celery -> svc-parse-docling),
    # чтобы пользователь видел прогресс обработки без ручного вызова /ingest/run.
    task_id: uuid.UUID | None = None
    if created_documents:
        task = IngestTask(
            status=IngestStatus.QUEUED,
            document_ids=[str(d.id) for d in created_documents],
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        tasks.enqueue_run(
            task.id,
            [d.id for d in created_documents],
            ProcessingLevel.L1.value,
        )
        task_id = task.id

    uploaded: list[DocumentFileSummary] = []
    for document in created_documents:
        summary = _to_summary(document)
        if task_id is not None:
            summary.latest_task_status = IngestStatus.QUEUED
            summary.latest_task_progress = 0.0
        uploaded.append(summary)

    return IngestUploadBatchResponse(data=uploaded, count=len(uploaded), task_id=task_id)


@router.post(
    "/run",
    response_model=IngestUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_ingest(session: SessionDep, body: IngestRunRequest) -> IngestUploadResponse:
    """POST /api/v1/ingest/run — (пере)запуск обработки на заданном уровне."""
    if not body.document_ids:
        raise HTTPException(status_code=400, detail="document_ids must not be empty")
    if body.level != ProcessingLevel.L1:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Processing level {body.level.value} is not implemented yet",
        )

    documents = session.exec(
        select(Document).where(Document.id.in_(body.document_ids))  # type: ignore[attr-defined]
    ).all()
    if len(documents) != len(body.document_ids):
        raise HTTPException(status_code=404, detail="One or more documents not found")

    task = IngestTask(
        status=IngestStatus.QUEUED,
        document_ids=[str(d) for d in body.document_ids],
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    tasks.enqueue_run(task.id, body.document_ids, body.level.value)

    return _to_response(task)


@router.get("/files", response_model=IngestFilesResponse)
def list_files(
    session: SessionDep,
    processing_level: ProcessingLevel | None = None,
    limit: int = 10,
    offset: int = 0,
) -> IngestFilesResponse:
    """GET /api/v1/ingest/files — список файлов и уровень обработки L0..L3."""
    return ingest_service.list_files(
        session,
        processing_level=processing_level,
        limit=limit,
        offset=offset,
    )


@router.get("/files/{document_id}", response_model=IngestFileDetailResponse)
def get_file_detail(
    session: SessionDep, document_id: uuid.UUID
) -> IngestFileDetailResponse:
    """GET /api/v1/ingest/files/{id} — метаданные + история задач."""
    detail = ingest_service.get_file_detail(session, document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return detail


@router.delete("/files/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(session: SessionDep, document_id: uuid.UUID) -> None:
    """DELETE /api/v1/ingest/files/{id} — удалить запись Document из БД."""
    if not ingest_service.delete_file(session, document_id):
        raise HTTPException(status_code=404, detail="Document not found")


@router.post(
    "/reindex",
    response_model=IngestUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reindex() -> IngestUploadResponse:
    """POST /api/v1/ingest/reindex — полная переиндексация (not implemented yet)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Full reindex is not implemented yet",
    )


@router.get("/status/{task_id}", response_model=IngestUploadResponse)
def get_status(session: SessionDep, task_id: uuid.UUID) -> IngestUploadResponse:
    """GET /api/v1/ingest/status/{task_id} — статус из Postgres."""
    task = session.get(IngestTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Ingest task not found")
    return _to_response(task)


@admin_router.get("/coverage", response_model=AdminCoverageResponse)
def admin_coverage(session: SessionDep) -> AdminCoverageResponse:
    """GET /api/v1/admin/coverage — агрегат покрытия корпуса по L0..L3."""
    try:
        return ingest_service.admin_coverage(session)
    except parser_client.ParserError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Parser statistics failed: {exc}",
        ) from exc


@admin_router.get("/raw-files", response_model=RawDataFilesResponse)
def list_raw_files(
    session: SessionDep,
    search: str = "",
    limit: int = 10,
    offset: int = 0,
) -> RawDataFilesResponse:
    """GET /api/v1/admin/raw-files — нераспаршенные PDF из SHARED/RAW_DATA."""
    try:
        return ingest_service.list_raw_data_files(
            session,
            search=search,
            limit=limit,
            offset=offset,
        )
    except parser_client.ParserError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Parser list failed: {exc}",
        ) from exc


@admin_router.post(
    "/raw-files/parse",
    response_model=IngestUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def parse_raw_file(
    session: SessionDep, body: ParseRawDataFileRequest
) -> IngestUploadResponse:
    """POST /api/v1/admin/raw-files/parse — зарегистрировать RAW_DATA PDF и поставить L1 в очередь."""
    try:
        document, task = ingest_service.parse_raw_data_file(session, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except parser_client.ParserError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Parser status failed: {exc}",
        ) from exc

    tasks.enqueue_run(task.id, [document.id], ProcessingLevel.L1.value)
    return _to_response(task)
