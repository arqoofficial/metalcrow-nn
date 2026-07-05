import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.models import ChatSession, Document
from app.models.litsearch import (
    FetchStatus,
    LiteraturePaper,
    LiteratureSearch,
    LitStage,
)
from tests.utils.user import create_random_user


def _make_session(db: Session) -> ChatSession:
    user = create_random_user(db)
    session = ChatSession(user_id=user.id, title="litsearch test session")
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_create_search_and_papers_with_defaults(db: Session) -> None:
    chat_session = _make_session(db)

    search = LiteratureSearch(session_id=chat_session.id, question="What is X?")
    db.add(search)
    db.commit()
    db.refresh(search)

    assert search.id is not None
    assert search.stage == LitStage.SEARCHING
    assert search.round == 0
    assert search.followup_of is None
    assert search.followup_search_id is None
    assert search.error is None
    assert search.created_at is not None
    assert search.updated_at is not None

    paper1 = LiteraturePaper(
        search_id=search.id,
        title="Paper One",
        authors="A. Author",
        abstract="An abstract.",
    )
    paper2 = LiteraturePaper(
        search_id=search.id,
        title="Paper Two",
        authors="B. Author",
        abstract="Another abstract.",
    )
    db.add(paper1)
    db.add(paper2)
    db.commit()
    db.refresh(paper1)
    db.refresh(paper2)

    assert paper1.id is not None
    assert paper2.id is not None
    assert paper1.fetch_status == FetchStatus.PENDING
    assert paper1.fulltext_status.value == "none"
    assert paper1.ingest_status.value == "none"
    assert paper1.fulltext_chars == 0
    assert paper1.document_id is None
    assert paper1.created_at is not None
    assert paper1.updated_at is not None


def test_document_id_unique_constraint(db: Session) -> None:
    chat_session = _make_session(db)

    search = LiteratureSearch(session_id=chat_session.id, question="Unique doc test?")
    db.add(search)
    db.commit()
    db.refresh(search)

    document = Document(minio_key="test/key.pdf", filename="key.pdf")
    db.add(document)
    db.commit()
    db.refresh(document)
    shared_document_id = document.id

    paper1 = LiteraturePaper(
        search_id=search.id,
        title="Paper One",
        authors="A. Author",
        abstract="An abstract.",
        document_id=shared_document_id,
    )
    paper2 = LiteraturePaper(
        search_id=search.id,
        title="Paper Two",
        authors="B. Author",
        abstract="Another abstract.",
        document_id=shared_document_id,
    )
    db.add(paper1)
    db.commit()

    db.add(paper2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_literature_paper_persists_fulltext_text(db: Session) -> None:
    from app.models.chat import ChatSession
    from app.models.litsearch import LiteraturePaper, LiteratureSearch
    from tests.utils.user import create_random_user

    user = create_random_user(db)
    cs = ChatSession(user_id=user.id, title="fulltext col test")
    db.add(cs)
    db.commit()
    db.refresh(cs)
    search = LiteratureSearch(session_id=cs.id, question="q?")
    db.add(search)
    db.commit()
    db.refresh(search)

    paper = LiteraturePaper(
        search_id=search.id,
        title="T",
        authors="A",
        abstract="abs",
        fulltext_text="the extracted full text",
    )
    db.add(paper)
    db.commit()
    db.refresh(paper)

    assert paper.fulltext_text == "the extracted full text"
    # default stays None when not provided
    other = LiteraturePaper(search_id=search.id, title="T2", authors="A", abstract="")
    db.add(other)
    db.commit()
    db.refresh(other)
    assert other.fulltext_text is None
