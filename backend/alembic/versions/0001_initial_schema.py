"""Create the six core TopGreenCloud tables."""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default="false", nullable=False),
        *timestamps(),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)
    op.create_table(
        "cloud_providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("slug", sa.String(120), nullable=False, unique=True),
        sa.Column("website_url", sa.Text()),
        sa.Column("description", sa.Text()),
        *timestamps(),
    )
    op.create_table(
        "sustainability_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_id", sa.Integer(), sa.ForeignKey("cloud_providers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric_name", sa.String(120), nullable=False),
        sa.Column("metric_value", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(80)),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_date", sa.Date()),
        sa.Column("notes", sa.Text()),
        *timestamps(),
    )
    op.create_table(
        "uploaded_bills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_id", sa.Integer(), sa.ForeignKey("cloud_providers.id", ondelete="SET NULL")),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False, unique=True),
        sa.Column("file_type", sa.String(80), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), server_default="uploaded", nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        *timestamps(),
        sa.CheckConstraint("file_size > 0", name="ck_uploaded_bills_file_size"),
        sa.UniqueConstraint("id", "user_id", name="uq_uploaded_bills_id_user"),
    )
    op.create_table(
        "carbon_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bill_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), sa.ForeignKey("cloud_providers.id", ondelete="SET NULL")),
        sa.Column("total_co2e", sa.Numeric(24, 9)),
        sa.Column("methodology_version", sa.String(120), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["bill_id", "user_id"], ["uploaded_bills.id", "uploaded_bills.user_id"],
            name="fk_carbon_analyses_bill_owner", ondelete="CASCADE",
        ),
        sa.CheckConstraint("total_co2e >= 0", name="ck_carbon_analyses_co2e"),
    )
    op.create_table(
        "carbon_analysis_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("carbon_analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service_name", sa.String(255), nullable=False),
        sa.Column("service_category", sa.String(120)),
        sa.Column("region", sa.String(120)),
        sa.Column("usage_quantity", sa.Numeric(24, 9)),
        sa.Column("usage_unit", sa.String(80)),
        sa.Column("estimated_co2e", sa.Numeric(24, 9)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("usage_quantity >= 0", name="ck_carbon_analysis_items_usage"),
        sa.CheckConstraint("estimated_co2e >= 0", name="ck_carbon_analysis_items_co2e"),
    )
    for table, columns in (
        ("sustainability_metrics", ["provider_id"]),
        ("uploaded_bills", ["user_id", "provider_id"]),
        ("carbon_analyses", ["user_id", "bill_id", "provider_id"]),
        ("carbon_analysis_items", ["analysis_id"]),
    ):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    op.drop_table("carbon_analysis_items")
    op.drop_table("carbon_analyses")
    op.drop_table("uploaded_bills")
    op.drop_table("sustainability_metrics")
    op.drop_table("cloud_providers")
    op.drop_table("users")
