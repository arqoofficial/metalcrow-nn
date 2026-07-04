import uuid

from sqlmodel import Field, SQLModel


# Database model, table `experiments.labs` (SPEC_V3 §4, ontology diagram)
class Lab(SQLModel, table=True):
    __tablename__ = "labs"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    organization: str | None = None
