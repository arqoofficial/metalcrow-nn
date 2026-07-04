import uuid

from sqlmodel import Field, SQLModel


# Database model, table `experiments.researchers` (SPEC_V3 §4, ontology diagram)
class Researcher(SQLModel, table=True):
    __tablename__ = "researchers"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    full_name: str
    lab_id: uuid.UUID | None = Field(default=None, foreign_key="experiments.labs.id")
    role: str | None = None
