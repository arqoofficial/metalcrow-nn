import uuid

from app.models.litsearch import FetchStatus, FulltextStatus, LitIngestStatus, LitStage
from app.schemas.chat import ChatMessageResponse, ChatMode
from app.schemas.litsearch import (
    LitAnswerRef,
    LiteraturePaperPublic,
    LiteratureRef,
    LiteratureSearchPublic,
    PaperIngestStatusPublic,
)


def test_chat_mode_has_literature_member() -> None:
    assert ChatMode.LITERATURE == "literature"


def test_chat_message_response_literature_field_round_trips() -> None:
    response = ChatMessageResponse(
        claims=[],
        summary="x",
        tools_used=[],
        session_id=uuid.uuid4(),
        literature=LiteratureRef(search_id=uuid.uuid4(), paper_count=2),
    )
    dumped = response.model_dump()
    assert dumped["literature"]["paper_count"] == 2


def test_chat_message_response_literature_defaults_to_none() -> None:
    response = ChatMessageResponse(
        claims=[], summary="x", tools_used=[], session_id=uuid.uuid4()
    )
    assert response.literature is None


def test_paper_ingest_status_public_constructible_with_defaults() -> None:
    status = PaperIngestStatusPublic()
    assert status.status == "none"
    assert status.progress == 0.0
    assert status.stage_name is None
    assert status.error is None


def test_literature_search_public_round_trips_with_nested_papers_and_answers() -> None:
    search_id = uuid.uuid4()
    paper = LiteraturePaperPublic(
        id=uuid.uuid4(),
        doi="10.1/xyz",
        title="A Paper",
        authors="A. Author",
        year=2024,
        abstract="An abstract.",
        pdf_url=None,
        citation_count=5,
        fetch_status=FetchStatus.DONE,
        fulltext_status=FulltextStatus.ADDED,
        fulltext_chars=1234,
        ingest_status=LitIngestStatus.DONE,
        document_id=uuid.uuid4(),
    )
    answer = LitAnswerRef(message_id=uuid.uuid4(), kind="abstracts")

    search = LiteratureSearchPublic(
        id=search_id,
        stage=LitStage.DONE,
        round=1,
        followup_search_id=None,
        papers=[paper],
        answers=[answer],
        queries=["What is the yield strength of Ti-6Al-4V?"],
    )

    dumped = search.model_dump()
    assert dumped["id"] == search_id
    assert dumped["stage"] == LitStage.DONE
    assert dumped["papers"][0]["title"] == "A Paper"
    assert dumped["papers"][0]["fetch_status"] == FetchStatus.DONE
    assert dumped["answers"][0]["kind"] == "abstracts"
    assert dumped["queries"] == ["What is the yield strength of Ti-6Al-4V?"]


def test_literature_search_public_queries_defaults_to_empty_list() -> None:
    search = LiteratureSearchPublic(
        id=uuid.uuid4(), stage=LitStage.SEARCHING, round=0
    )
    assert search.queries == []
