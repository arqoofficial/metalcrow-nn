"""Lowercase ingeststatus enum values to match StrEnum .value and workers.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-03 15:55:00.000000

"""

from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

_RENAMES = (
    ("QUEUED", "queued"),
    ("PARSE", "parse"),
    ("NORMALIZE", "normalize"),
    ("DEDUP_LINK", "dedup_link"),
    ("LOAD", "load"),
    ("BUILD_FLAT", "build_flat"),
    ("EMBED", "embed"),
    ("SYNC_NEO4J", "sync_neo4j"),
    ("BUILD_WIKI", "build_wiki"),
    ("DONE", "done"),
    ("ERROR", "error"),
)


def upgrade() -> None:
    for old, new in _RENAMES:
        op.execute(f"ALTER TYPE ingeststatus RENAME VALUE '{old}' TO '{new}'")


def downgrade() -> None:
    for old, new in reversed(_RENAMES):
        op.execute(f"ALTER TYPE ingeststatus RENAME VALUE '{new}' TO '{old}'")
