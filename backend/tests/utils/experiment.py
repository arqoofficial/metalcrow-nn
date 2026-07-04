import uuid

from sqlalchemy import text
from sqlmodel import Session

from app.models import (
    Document,
    Equipment,
    Experiment,
    Lab,
    Material,
    MaterialType,
    Property,
    Regime,
    Researcher,
    Result,
)
from tests.utils.utils import random_lower_string


def refresh_experiments_flat(db: Session) -> None:
    """search/graph/analytics читают `experiments.experiments_flat`, а не базовые
    таблицы напрямую — вьюха обновляется только явным REFRESH (см. worker BUILD-FLAT,
    §7), поэтому тестовым фикстурам приходится делать это вручную."""
    db.execute(text("REFRESH MATERIALIZED VIEW experiments.experiments_flat"))
    db.commit()


def create_full_experiment(
    db: Session,
    *,
    material_name: str | None = None,
    property_name: str | None = None,
    temperature: float | None = 500.0,
    value: float = 42.0,
) -> Experiment:
    """Создаёт эксперимент со всеми связанными сущностями (материал/режим/лаборатория/
    исследователь/оборудование/документ/результат) и рефрешит `experiments_flat`."""
    material = Material(
        name=material_name or random_lower_string(), material_type=MaterialType.ALLOY
    )
    regime = Regime(temperature=temperature, pressure=101325.0, medium="air")
    lab = Lab(name=random_lower_string())
    researcher = Researcher(full_name=random_lower_string())
    equipment = Equipment(name=random_lower_string())
    document = Document(parser_path=f"UPLOAD_DATA/metalcrow/{uuid.uuid4()}/doc.pdf", filename="doc.pdf")
    property_ = Property(name=property_name or random_lower_string())
    db.add_all([material, regime, lab, researcher, equipment, document, property_])
    db.commit()
    for obj in (material, regime, lab, researcher, equipment, document, property_):
        db.refresh(obj)

    experiment = Experiment(
        title=random_lower_string(),
        material_id=material.id,
        regime_id=regime.id,
        equipment_id=equipment.id,
        lab_id=lab.id,
        researcher_id=researcher.id,
        document_id=document.id,
        description=random_lower_string(),
    )
    db.add(experiment)
    db.commit()
    db.refresh(experiment)

    result = Result(
        experiment_id=experiment.id, property_id=property_.id, value=value, unit="MPa"
    )
    db.add(result)
    db.commit()

    refresh_experiments_flat(db)
    return experiment
