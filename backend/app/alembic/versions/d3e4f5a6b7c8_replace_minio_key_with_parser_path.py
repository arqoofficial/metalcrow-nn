"""Replace minio_key with parser_path on documents (parser single file source)."""

from alembic import op
import sqlalchemy as sa

revision = "d3e4f5a6b7c8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("parser_path", sa.String(), nullable=True),
        schema="experiments",
    )
    op.execute(
        "UPDATE experiments.documents SET parser_path = minio_key WHERE parser_path IS NULL"
    )
    op.alter_column(
        "documents",
        "parser_path",
        nullable=False,
        schema="experiments",
    )
    op.drop_column("documents", "minio_key", schema="experiments")


def downgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("minio_key", sa.String(), nullable=True),
        schema="experiments",
    )
    op.execute(
        "UPDATE experiments.documents SET minio_key = parser_path WHERE minio_key IS NULL"
    )
    op.alter_column(
        "documents",
        "minio_key",
        nullable=False,
        schema="experiments",
    )
    op.drop_column("documents", "parser_path", schema="experiments")
