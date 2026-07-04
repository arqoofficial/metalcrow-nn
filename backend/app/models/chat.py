import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime
from sqlmodel import JSON, Column, Field, SQLModel

from app.models.common import get_datetime_utc


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


# Database model, table `chat_session` (schema `public`, SPEC_V3 §4/§8.4)
class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_session"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    title: str | None = None
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class ChatSessionPublic(SQLModel):
    id: uuid.UUID
    title: str | None = None
    created_at: datetime | None = None


# Database model, table `chat_message` (schema `public`, SPEC_V3 §4/§8.4)
class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_message"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(
        foreign_key="chat_session.id", nullable=False, ondelete="CASCADE"
    )
    role: ChatRole
    content: str
    # запрос: {trigger, gap_cell} (D.3); ответ ассистента: {claims, summary, tools_used, subgraph} (D.4)
    message_metadata: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON)
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
