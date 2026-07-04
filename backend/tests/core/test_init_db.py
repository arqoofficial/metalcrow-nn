from sqlmodel import Session, select

from app import crud
from app.core.config import settings
from app.core.db import init_db
from app.core.security import verify_password
from app.models import User, UserUpdate


def test_init_db_repairs_existing_first_superuser(db: Session) -> None:
    user = db.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).one()
    crud.update_user(
        session=db,
        db_user=user,
        user_in=UserUpdate(
            is_superuser=False,
            password="wrong-password-123",
        ),
    )

    init_db(db)

    repaired = db.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).one()
    password_ok, _ = verify_password(
        settings.FIRST_SUPERUSER_PASSWORD, repaired.hashed_password
    )
    assert repaired.is_superuser is True
    assert password_ok is True
