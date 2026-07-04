"""Add processing_level and okf_raw_path to documents (SPEC_V5 §4)."""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "3153c73c17fe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "processing_level",
            sa.String(),
            nullable=False,
            server_default="L0",
        ),
        schema="experiments",
    )
    op.add_column(
        "documents",
        sa.Column("okf_raw_path", sa.String(), nullable=True),
        schema="experiments",
    )


def downgrade() -> None:
    op.drop_column("documents", "okf_raw_path", schema="experiments")
    op.drop_column("documents", "processing_level", schema="experiments")
