"""add literature_papers.fulltext_text

Revision ID: 443ea26fef35
Revises: a43ee2bced5c
Create Date: 2026-07-04

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


revision = "443ea26fef35"
down_revision = "a43ee2bced5c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "literature_papers",
        sa.Column("fulltext_text", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        schema="experiments",
    )


def downgrade():
    op.drop_column("literature_papers", "fulltext_text", schema="experiments")
