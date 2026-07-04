import uuid
from enum import StrEnum

from pydantic import BaseModel
from sqlmodel import Field, SQLModel

from app.models.chat import ChatSessionPublic
from app.schemas.common import RegimeBucket
from app.schemas.graph import SubgraphResponse

__all__ = [
    "ChatTrigger",
    "ChatMode",
    "GapCell",
    "ChatSessionCreate",
    "ChatSessionsPublic",
    "ChatMessageMetadata",
    "ChatMessageRequest",
    "ClaimConfidence",
    "ClaimKind",
    "ClaimRisk",
    "ClaimGapCell",
    "ChatSource",
    "Claim",
    "ChatMessageResponse",
]


class ChatTrigger(StrEnum):
    GAP_CLICK = "gap_click"
    USER_INPUT = "user_input"


# Явный выбор пользователя, какой источник знаний должен отвечать (см.
# app/services/chat.py::answer_message). AUTO — текущее поведение по
# умолчанию (приоритетный waterfall ontology → knowledge_graph).
class ChatMode(StrEnum):
    AUTO = "auto"
    ONTOLOGY = "ontology"
    KNOWLEDGE_GRAPH = "knowledge_graph"


# gap-cell из аналитики пробелов, передаётся в chat при клике на heatmap (SPEC_V3 §8.4/D.3)
class GapCell(SQLModel):
    material_id: uuid.UUID
    material: str
    property: str
    regime_bucket: RegimeBucket


# POST /api/v1/chat/sessions request body
class ChatSessionCreate(SQLModel):
    title: str | None = None


class ChatSessionsPublic(SQLModel):
    data: list[ChatSessionPublic]
    count: int


class ChatMessageMetadata(SQLModel):
    trigger: ChatTrigger | None = None
    gap_cell: GapCell | None = None
    mode: ChatMode = ChatMode.AUTO


# POST /api/v1/chat/sessions/{id}/messages request body (Приложение D.3).
# pydantic.BaseModel, а не SQLModel: поле `metadata` коллизирует с зарезервированным
# SQLAlchemy declarative-атрибутом `SQLModel.metadata` (есть даже у table=False моделей).
class ChatMessageRequest(BaseModel):
    content: str = Field(min_length=1)
    metadata: ChatMessageMetadata | None = None


class ClaimConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ClaimKind(StrEnum):
    FACT = "fact"
    HYPOTHESIS = "hypothesis"


class ClaimRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# D.4 не объявляет gap_cell.material_id/required — упрощённая форма в отличие от GapCell выше
class ClaimGapCell(SQLModel):
    material: str | None = None
    property: str | None = None
    regime_bucket: str | None = None


# GraphRAG source article, derived from science-knowledge-graph's
# RAGResponse.sources doc-ids (see app/services/chat.py::_resolve_chat_sources).
# Rendered as a clickable chip in the chat UI (both live answer and history)
# that deep-links to the wiki document view: /wiki?doc=<okf_path>.
class ChatSource(SQLModel):
    doc_id: str
    filename: str | None = None
    # raw source path under SHARED/, e.g. "RAW_DATA/Обзоры/Медный купорос.pdf"
    source_path: str | None = None
    # stage-1 OKF markdown path the wiki is keyed by, e.g.
    # "01_docling_clean00/RAW_DATA/Обзоры/Медный купорос.pdf.md" — the deep-link
    # target; parser_client.okf_to_raw_path inverts it for raw/PDF download.
    okf_path: str | None = None


# Атомарное утверждение агента, claim schema из SPEC_V3 §8.4 (колонки P0/P1/P2)
class Claim(SQLModel):
    text: str
    experiment_ids: list[uuid.UUID]
    confidence: ClaimConfidence
    kind: ClaimKind = ClaimKind.FACT  # P1
    gap_cell: ClaimGapCell | None = None  # P1
    novelty: float | None = Field(default=None, ge=0, le=1)  # TODO(SPEC_V3 §5.7): P2
    risk: ClaimRisk | None = None  # TODO(SPEC_V3 §5.7): P2, LLM risk assessment
    value: float | None = Field(default=None, ge=0, le=1)  # TODO(SPEC_V3 §5.7): P2
    score_rationale: str | None = None  # TODO(SPEC_V3 §5.7): P2, LLM rationale
    sources: list[ChatSource] = Field(default_factory=list)


# SSE event payload for POST /api/v1/chat/sessions/{id}/messages (Приложение D.4)
class ChatMessageResponse(SQLModel):
    claims: list[Claim]
    summary: str
    tools_used: list[str]
    subgraph: SubgraphResponse | None = None  # TODO(SPEC_V3 §5.7 P2): opt-in mini-graph
    session_id: uuid.UUID
    # Какой источник знаний фактически ответил: "ontology" | "knowledge_graph" |
    # "hypothesis" (gap_click). Позволяет фронтенду явно показать пользователю,
    # что сработало, даже когда request.metadata.mode == "auto".
    mode_used: str = "knowledge_graph"
