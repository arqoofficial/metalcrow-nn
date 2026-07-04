import uuid

from sqlmodel import Field, SQLModel


# Database model, table `experiments.equipment` (SPEC_V3 §4, ontology diagram)
class Equipment(SQLModel, table=True):
    __tablename__ = "equipment"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    type: str | None = None
    lab_id: uuid.UUID | None = Field(default=None, foreign_key="experiments.labs.id")
