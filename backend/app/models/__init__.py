"""SQLModel table models, one file per domain (SPEC_V3 §4/§10).

Reexports everything so existing call sites keep using `from app.models import X`
without caring about the underlying per-domain module layout.
"""

from sqlmodel import SQLModel

from app.models.chat import ChatMessage, ChatRole, ChatSession, ChatSessionPublic
from app.models.common import (
    Message,
    NewPassword,
    Token,
    TokenPayload,
    get_datetime_utc,
)
from app.models.documents import Document, ProcessingLevel
from app.models.entity_aliases import EntityAlias, EntitySameAs
from app.models.equipment import Equipment
from app.models.experiments import Experiment, Result
from app.models.ingest import IngestStatus, IngestTask
from app.models.labs import Lab
from app.models.materials import EMBEDDING_DIM, Material, MaterialPublic, MaterialType
from app.models.properties import Property
from app.models.regimes import Regime, RegimePublic
from app.models.researchers import Researcher
from app.models.users import (
    UpdatePassword,
    User,
    UserBase,
    UserCreate,
    UserPublic,
    UserRegister,
    UsersPublic,
    UserUpdate,
    UserUpdateMe,
)

__all__ = [
    "SQLModel",
    "get_datetime_utc",
    "Message",
    "NewPassword",
    "Token",
    "TokenPayload",
    "User",
    "UserBase",
    "UserCreate",
    "UserRegister",
    "UserUpdate",
    "UserUpdateMe",
    "UpdatePassword",
    "UserPublic",
    "UsersPublic",
    "MaterialType",
    "Material",
    "MaterialPublic",
    "EMBEDDING_DIM",
    "EntityAlias",
    "EntitySameAs",
    "Regime",
    "RegimePublic",
    "Property",
    "Equipment",
    "Lab",
    "Researcher",
    "Document",
    "ProcessingLevel",
    "Experiment",
    "Result",
    "IngestStatus",
    "IngestTask",
    "ChatRole",
    "ChatSession",
    "ChatSessionPublic",
    "ChatMessage",
]
