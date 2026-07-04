"""create experiments_flat materialized view

Search projection (SPEC_V3 §4) denormalizing Experiment + joined dimensions
for hybrid search / analytics. Rebuilt wholesale on each reindex
(`REFRESH MATERIALIZED VIEW`, worker-etl BUILD-FLAT stage, §7) — no
incremental updates.

Revision ID: c4a38db39e1f
Revises: f8bfd1e20554
Create Date: 2026-07-02 13:12:10.448600

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4a38db39e1f"
down_revision = "f8bfd1e20554"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE MATERIALIZED VIEW experiments.experiments_flat AS
        SELECT
            e.id,
            e.title,
            m.name          AS material_name,
            m.formula       AS material_formula,
            m.smiles        AS material_smiles,
            m.composition   AS material_composition,
            m.material_type,
            r.temperature, r.pressure, r.duration, r.medium,
            to_jsonb(r)     AS regime_json,
            p.name          AS property_name,
            res.value       AS property_value,
            res.unit        AS property_unit,
            res.uncertainty,
            eq.name         AS equipment_name,
            l.name          AS lab_name,
            rs.full_name    AS researcher,
            e.description   AS conclusion,
            d.filename      AS source_doc,
            e.source_page,
            e.source_paragraph,
            e.tags,
            e.embedding,
            e.created_at
        FROM experiments.experiments e
        LEFT JOIN experiments.materials m ON e.material_id = m.id
        LEFT JOIN experiments.regimes r ON e.regime_id = r.id
        LEFT JOIN experiments.results res ON res.experiment_id = e.id
        LEFT JOIN experiments.properties p ON res.property_id = p.id
        LEFT JOIN experiments.equipment eq ON e.equipment_id = eq.id
        LEFT JOIN experiments.labs l ON e.lab_id = l.id
        LEFT JOIN experiments.researchers rs ON e.researcher_id = rs.id
        LEFT JOIN experiments.documents d ON e.document_id = d.id
        """
    )
    op.execute(
        "CREATE INDEX idx_flat_embedding ON experiments.experiments_flat "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX idx_flat_material ON experiments.experiments_flat (material_name)"
    )
    op.execute(
        "CREATE INDEX idx_flat_property ON experiments.experiments_flat (property_name)"
    )
    op.execute(
        "CREATE INDEX idx_flat_tags ON experiments.experiments_flat USING GIN (tags)"
    )


def downgrade():
    op.execute("DROP MATERIALIZED VIEW IF EXISTS experiments.experiments_flat")
