from sqlmodel import Session

from app.models import Material, MaterialType
from tests.utils.utils import random_lower_string


def create_random_material(
    db: Session, *, material_type: MaterialType = MaterialType.ALLOY
) -> Material:
    material = Material(name=random_lower_string(), material_type=material_type)
    db.add(material)
    db.commit()
    db.refresh(material)
    return material
