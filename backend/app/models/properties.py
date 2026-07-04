import uuid

from sqlmodel import Field, SQLModel


# Database model, table `experiments.properties` (SPEC_V3 §4)
class Property(SQLModel, table=True):
    __tablename__ = "properties"
    __table_args__ = {"schema": "experiments"}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(unique=True)
    unit: str | None = None
    category: str | None = None
