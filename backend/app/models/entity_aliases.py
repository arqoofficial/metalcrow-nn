import uuid

from sqlmodel import Field, SQLModel


# Database model, table `experiments.entity_aliases` (SPEC_V3 §4)
class EntityAlias(SQLModel, table=True):
    __tablename__ = "entity_aliases"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    entity_type: str  # 'material' | 'property' | 'regime' | ...
    entity_id: uuid.UUID
    alias: str
    source: str | None = None


# Database model, table `experiments.entity_same_as` (SPEC_V3 §4)
class EntitySameAs(SQLModel, table=True):
    __tablename__ = "entity_same_as"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    entity_type: str
    source_id: uuid.UUID
    canonical_id: uuid.UUID
    confidence: float = 1.0
    method: str | None = None  # 'exact_alias' | 'embedding' | 'manual'
