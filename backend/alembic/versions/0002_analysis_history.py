"""Add immutable carbon analysis audit snapshots."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("carbon_analyses", sa.Column("provider_slug", sa.String(120)))
    op.add_column("carbon_analyses", sa.Column("complete", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.alter_column("carbon_analyses", "total_co2e", existing_type=sa.Numeric(24, 9),
                    type_=sa.Numeric(52, 18), existing_nullable=True)

    op.add_column("carbon_analysis_items", sa.Column("provider_slug", sa.String(1000)))
    op.add_column("carbon_analysis_items", sa.Column("kg_co2e_per_unit", sa.Numeric(24, 9)))
    op.add_column("carbon_analysis_items", sa.Column("source_name", sa.String(1000)))
    op.add_column("carbon_analysis_items", sa.Column("source_url", sa.Text()))
    op.add_column("carbon_analysis_items", sa.Column("source_methodology_version", sa.String(1000)))
    op.add_column("carbon_analysis_items", sa.Column("source_date", sa.Date()))
    op.add_column("carbon_analysis_items", sa.Column("unsupported_reason", sa.Text()))
    op.alter_column("carbon_analysis_items", "service_name", existing_type=sa.String(255),
                    type_=sa.String(1000), existing_nullable=False, nullable=True)
    op.alter_column("carbon_analysis_items", "service_category", existing_type=sa.String(120),
                    type_=sa.String(1000), existing_nullable=True)
    op.alter_column("carbon_analysis_items", "region", existing_type=sa.String(120),
                    type_=sa.String(1000), existing_nullable=True)
    op.alter_column("carbon_analysis_items", "usage_unit", existing_type=sa.String(80),
                    type_=sa.String(1000), existing_nullable=True)
    op.alter_column("carbon_analysis_items", "estimated_co2e", existing_type=sa.Numeric(24, 9),
                    type_=sa.Numeric(48, 18), existing_nullable=True)


def downgrade():
    # This non-truncating downgrade can fail if Phase 12 rows contain null service
    # names or exceed the former widths/precision; operators must resolve them first.
    op.alter_column("carbon_analysis_items", "estimated_co2e", existing_type=sa.Numeric(48, 18),
                    type_=sa.Numeric(24, 9), existing_nullable=True)
    op.alter_column("carbon_analysis_items", "usage_unit", existing_type=sa.String(1000),
                    type_=sa.String(80), existing_nullable=True)
    op.alter_column("carbon_analysis_items", "region", existing_type=sa.String(1000),
                    type_=sa.String(120), existing_nullable=True)
    op.alter_column("carbon_analysis_items", "service_category", existing_type=sa.String(1000),
                    type_=sa.String(120), existing_nullable=True)
    op.alter_column("carbon_analysis_items", "service_name", existing_type=sa.String(1000),
                    type_=sa.String(255), existing_nullable=True, nullable=False)
    for column in ("unsupported_reason", "source_date", "source_methodology_version",
                   "source_url", "source_name", "kg_co2e_per_unit", "provider_slug"):
        op.drop_column("carbon_analysis_items", column)
    op.alter_column("carbon_analyses", "total_co2e", existing_type=sa.Numeric(52, 18),
                    type_=sa.Numeric(24, 9), existing_nullable=True)
    op.drop_column("carbon_analyses", "complete")
    op.drop_column("carbon_analyses", "provider_slug")
