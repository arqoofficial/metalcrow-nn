import uuid
from collections.abc import Generator

from fastapi import APIRouter, HTTPException, status
from sqlmodel import col, select
from starlette.responses import StreamingResponse

from app.api.deps import CurrentUser, SessionDep
from app.models import ChatMessage, ChatSession, ChatSessionPublic
from app.schemas.chat import ChatMessageRequest, ChatSessionCreate, ChatSessionsPublic
from app.services import chat as chat_service

router = APIRouter(prefix="/chat", tags=["chat"])


def _get_owned_session(
    session: SessionDep, current_user: CurrentUser, session_id: uuid.UUID
) -> ChatSession:
    chat_session = session.get(ChatSession, session_id)
    if chat_session is None or chat_session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return chat_session


@router.post("/sessions", response_model=ChatSessionPublic)
def create_session(
    session: SessionDep, current_user: CurrentUser, body: ChatSessionCreate
) -> ChatSession:
    """POST /api/v1/chat/sessions — создать сессию (SPEC_V3 §8.4)."""
    if not (body.title or "").strip():
        reusable = chat_service.find_reusable_empty_session(session, current_user.id)
        if reusable is not None:
            return reusable
    chat_session = ChatSession(user_id=current_user.id, title=body.title)
    session.add(chat_session)
    session.commit()
    session.refresh(chat_session)
    return chat_session


@router.get("/sessions", response_model=ChatSessionsPublic)
def list_sessions(session: SessionDep, current_user: CurrentUser) -> ChatSessionsPublic:
    """GET /api/v1/chat/sessions — список сессий текущего пользователя."""
    statement = (
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        .order_by(col(ChatSession.created_at).desc())
    )
    sessions = session.exec(statement).all()
    return ChatSessionsPublic(
        data=[ChatSessionPublic.model_validate(s) for s in sessions],
        count=len(sessions),
    )


@router.get("/sessions/{session_id}", response_model=list[ChatMessage])
def get_session_history(
    session: SessionDep, current_user: CurrentUser, session_id: uuid.UUID
) -> list[ChatMessage]:
    """GET /api/v1/chat/sessions/{id} — история сессии (user/assistant сообщения)."""
    _get_owned_session(session, current_user, session_id)
    statement = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(col(ChatMessage.created_at))
    )
    messages = list(session.exec(statement).all())
    for message in messages:
        if message.message_metadata:
            message.message_metadata = chat_service.refresh_stored_message_sources(
                message.message_metadata
            )
    return messages


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session: SessionDep, current_user: CurrentUser, session_id: uuid.UUID
) -> None:
    """DELETE /api/v1/chat/sessions/{id} — удалить сессию и её сообщения."""
    chat_session = _get_owned_session(session, current_user, session_id)
    chat_service.delete_session(session, chat_session)


@router.post("/sessions/{session_id}/messages")
def post_message(
    session: SessionDep,
    current_user: CurrentUser,
    session_id: uuid.UUID,
    body: ChatMessageRequest,
) -> StreamingResponse:
    """POST /api/v1/chat/sessions/{id}/messages — SSE stream (§8.4).

    nginx для `/api/v1/chat/` держит `proxy_buffering off` (см. frontend/nginx.conf).
    TODO(SPEC_V3 §5.7): реальный token-by-token LLM-стриминг; сейчас один SSE-event
    с полным JSON-ответом (`ChatMessageResponse`) после синхронной обработки.
    """
    _get_owned_session(session, current_user, session_id)
    response = chat_service.answer_message(
        session, session_id, body, user_id=current_user.id
    )

    def event_stream() -> Generator[str, None, None]:
        yield f"data: {response.model_dump_json()}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
