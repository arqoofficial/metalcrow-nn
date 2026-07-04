import uuid

from sqlmodel import Session, col, func, select

from app.models import Document, IngestStatus, IngestTask, ProcessingLevel
from app.services import parser_client
from app.schemas.ingest import (
    AdminCoverageResponse,
    DocumentFileSummary,
    IngestFileDetailResponse,
    IngestFileHistoryItem,
    IngestFilesResponse,
    ParseRawDataFileRequest,
    ProcessingLevelCoverage,
    RawDataFileSummary,
    RawDataFilesResponse,
)


def _latest_task_for_document(
    tasks: list[IngestTask], document_id: uuid.UUID
) -> IngestTask | None:
    """`tasks` должен быть отсортирован по created_at DESC — берём первое совпадение."""
    doc_id = str(document_id)
    for task in tasks:
        if task.document_ids and doc_id in task.document_ids:
            return task
    return None


def _document_summary(
    document: Document, latest_task: IngestTask | None = None
) -> DocumentFileSummary:
    return DocumentFileSummary(
        id=document.id,
        filename=document.filename,
        mime_type=document.mime_type,
        processing_level=document.processing_level,
        okf_raw_path=document.okf_raw_path,
        uploaded_at=document.uploaded_at,
        latest_task_status=latest_task.status if latest_task else None,
        latest_task_progress=latest_task.progress if latest_task else None,
        latest_task_stage=latest_task.stage_name if latest_task else None,
        latest_task_error=latest_task.error if latest_task else None,
    )


def _recent_tasks(session: Session) -> list[IngestTask]:
    return session.exec(
        select(IngestTask).order_by(col(IngestTask.created_at).desc()).limit(500)
    ).all()


def list_files(
    session: Session,
    *,
    processing_level: ProcessingLevel | None = None,
    limit: int = 10,
    offset: int = 0,
) -> IngestFilesResponse:
    statement = select(Document).order_by(col(Document.uploaded_at).desc())
    if processing_level is not None:
        statement = statement.where(Document.processing_level == processing_level)
    statement = statement.offset(offset).limit(limit)
    documents = session.exec(statement).all()
    count = session.exec(select(func.count()).select_from(Document)).one()
    tasks = _recent_tasks(session)
    return IngestFilesResponse(
        data=[
            _document_summary(doc, _latest_task_for_document(tasks, doc.id))
            for doc in documents
        ],
        count=count,
    )


def get_file_detail(session: Session, document_id: uuid.UUID) -> IngestFileDetailResponse | None:
    document = session.get(Document, document_id)
    if document is None:
        return None

    tasks = session.exec(
        select(IngestTask).order_by(col(IngestTask.created_at).desc())
    ).all()
    doc_id = str(document_id)
    history: list[IngestFileHistoryItem] = []
    for task in tasks:
        if not task.document_ids or doc_id not in task.document_ids:
            continue
        history.append(
            IngestFileHistoryItem(
                task_id=task.id,
                status=task.status,
                stage_name=task.stage_name,
                progress=task.progress,
                error=task.error,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
        )

    return IngestFileDetailResponse(
        document=_document_summary(document, _latest_task_for_document(tasks, document_id)),
        history=history,
    )


def admin_coverage(session: Session) -> AdminCoverageResponse:
    stats = parser_client.get_statistics()
    total = stats.total_raw_files

    l2 = session.exec(
        select(func.count())
        .select_from(Document)
        .where(Document.processing_level == ProcessingLevel.L2)
    ).one()
    l3 = session.exec(
        select(func.count())
        .select_from(Document)
        .where(Document.processing_level == ProcessingLevel.L3)
    ).one()

    # L1/L0 from parser SHARED coverage; L2/L3 remain backend processing levels.
    l1 = max(0, stats.stage1_done - l2 - l3)
    l0 = max(0, total - stats.stage1_done)
    counts = {
        ProcessingLevel.L0: l0,
        ProcessingLevel.L1: l1,
        ProcessingLevel.L2: l2,
        ProcessingLevel.L3: l3,
    }

    by_level: list[ProcessingLevelCoverage] = []
    for level in ProcessingLevel:
        count = counts[level]
        percent = (count / total * 100.0) if total else 0.0
        by_level.append(
            ProcessingLevelCoverage(level=level, count=count, percent=round(percent, 2))
        )
    return AdminCoverageResponse(total_files=total, by_level=by_level)


def delete_file(session: Session, document_id: uuid.UUID) -> bool:
    document = session.get(Document, document_id)
    if document is None:
        return False
    session.delete(document)
    session.commit()
    return True


def list_raw_data_files(
    session: Session,
    *,
    search: str = "",
    limit: int = 10,
    offset: int = 0,
) -> RawDataFilesResponse:
    listing = parser_client.list_raw_files(
        source="RAW_DATA",
        search=search,
        extension=".pdf",
        unparsed_only=True,
        offset=offset,
        limit=limit,
    )
    paths = [item.path for item in listing.data]
    documents_by_path: dict[str, Document] = {}
    if paths:
        documents = session.exec(
            select(Document).where(col(Document.parser_path).in_(paths))  # type: ignore[attr-defined]
        ).all()
        documents_by_path = {doc.parser_path: doc for doc in documents}

    tasks = _recent_tasks(session)
    data: list[RawDataFileSummary] = []
    for item in listing.data:
        document = documents_by_path.get(item.path)
        latest_task = (
            _latest_task_for_document(tasks, document.id) if document is not None else None
        )
        data.append(
            RawDataFileSummary(
                path=item.path,
                filename=item.filename,
                stage0_done=item.stage0_done,
                stage1_done=item.stage1_done,
                document_id=document.id if document is not None else None,
                processing_level=document.processing_level if document is not None else None,
                latest_task_status=latest_task.status if latest_task else None,
            )
        )
    return RawDataFilesResponse(
        data=data,
        count=listing.count,
        offset=listing.offset,
        limit=listing.limit,
    )


def parse_raw_data_file(
    session: Session,
    body: ParseRawDataFileRequest,
) -> tuple[Document, IngestTask]:
    path = body.path.strip()
    if not path.startswith("RAW_DATA/"):
        raise ValueError("path must start with RAW_DATA/")

    try:
        status = parser_client.get_status(path)
    except parser_client.ParserError as exc:
        raise ValueError(f"parser path not found: {path}") from exc

    document = session.exec(
        select(Document).where(Document.parser_path == status.resolved_path)
    ).first()
    if document is None:
        document = Document(
            parser_path=status.resolved_path,
            filename=status.resolved_path.rsplit("/", 1)[-1],
            mime_type="application/pdf",
            processing_level=ProcessingLevel.L0,
        )
        session.add(document)
        session.commit()
        session.refresh(document)
    elif (
        not body.enforce
        and document.processing_level != ProcessingLevel.L0
        and document.okf_raw_path
    ):
        raise ValueError("document already parsed; pass enforce=true to reprocess")

    task = IngestTask(
        status=IngestStatus.QUEUED,
        document_ids=[str(document.id)],
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return document, task
