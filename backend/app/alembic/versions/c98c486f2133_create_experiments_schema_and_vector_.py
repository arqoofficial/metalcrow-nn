"""create experiments schema and vector extension

SPEC_V3 §3 п.12 / §4: `CREATE EXTENSION vector` требует образ
`pgvector/pgvector:pg18` (не ванильный `postgres:18`); домен живёт в
отдельной схеме `experiments`, чтобы не смешиваться с `public.user`/`item`/
`chat_*` из шаблона.

Revision ID: c98c486f2133
Revises: fe56fa70289e
Create Date: 2026-07-02 13:10:49.970969

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "c98c486f2133"
down_revision = "fe56fa70289e"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS experiments")


def downgrade():
    op.execute("DROP SCHEMA IF EXISTS experiments CASCADE")
    op.execute("DROP EXTENSION IF EXISTS vector")
