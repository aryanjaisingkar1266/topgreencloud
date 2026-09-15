"""Curated provider-published reference data; reviewed 2026-09-15."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

PROVIDERS = (
    ("aws", "AWS", "https://aws.amazon.com/", "Amazon Web Services cloud computing platform."),
    ("azure", "Microsoft Azure", "https://azure.microsoft.com/", "Microsoft's cloud computing platform."),
    ("gcp", "Google Cloud", "https://cloud.google.com/", "Google's cloud computing platform."),
)
AWS_REPORT = "https://sustainability.aboutamazon.com/2025-amazon-sustainability-report.pdf"
AWS_EFFICIENCY = "https://aws.amazon.com/sustainability/"
MS_REPORT = "https://www.microsoft.com/en-us/corporate-responsibility/topics/sustainability/report/"
MS_EFFICIENCY = "https://datacenters.microsoft.com/sustainability/efficiency/"
GOOGLE_REPORT = "https://sustainability.google/google-2026-environmental-report/"

# Reserved provenance marker: do not reuse it for independently managed metrics.
# Downgrade matches the entire insertion payload, not just a name or source URL.
MARKER = "TopGreenCloud curated reference 0003: "
# Publication days are not established; dates remain null, periods are in notes.
METRICS = (
    ("aws", "Global data center PUE", "1.14", None, AWS_EFFICIENCY,
     "2025 global AWS average. PUE compares facility energy with IT energy; not an emissions factor."),
    ("aws", "Global data center WUE (water withdrawn)", "0.12", "L/kWh", AWS_EFFICIENCY,
     "2025 AWS global water withdrawal per kWh of IT load. Water withdrawal is not interchangeable with consumption."),
    ("aws", "Amazon renewable electricity matching", "100 percent matched", None, AWS_REPORT,
     "2025 Amazon global operations, including AWS; parent-company measure. Annual matching, not continuous renewable power at every site. Report pp. 5, 7 and 50."),
    ("azure", "Global data center PUE", "1.17", None, MS_EFFICIENCY,
     "FY25 (2024-07-01 to 2025-06-30). Microsoft fully owned and controlled datacenters operational for 12 months at calculation."),
    ("azure", "Global data center WUE (cooling and humidification)", "0.27", "L/kWh", MS_EFFICIENCY,
     "FY25 (2024-07-01 to 2025-06-30). Annual cooling/humidification water per IT kWh; fully owned and controlled Microsoft datacenters operational for 12 months."),
    ("azure", "Microsoft renewable electricity matching", "100 percent matched", None, MS_REPORT,
     "FY25 (2024-07-01 to 2025-06-30), reported in the 2026 report. Microsoft-wide annual electricity matching, not Azure-only or hourly carbon-free supply."),
    ("gcp", "Google net-new clean energy agreements", "Over 12", "GW", GOOGLE_REPORT,
     "2025 Google-wide contracts, not delivered electricity. Includes power purchase, energy storage and environmental attribute certificate agreements; actual generation can differ."),
    ("gcp", "Data center operational waste diverted from disposal", "88", "percent", GOOGLE_REPORT,
     "2025 global Google-owned and operated data centers; not a cloud-service-specific metric. 2026 Environmental Report."),
    ("gcp", "Google freshwater consumption replenished", "Roughly 78", "percent", GOOGLE_REPORT,
     "2025 Google-wide water stewardship replenishment relative to total freshwater consumption; not GCP-only or WUE. 2026 Environmental Report."),
)

providers = sa.table("cloud_providers", sa.column("id", sa.Integer),
    sa.column("slug", sa.String), sa.column("name", sa.String),
    sa.column("website_url", sa.Text), sa.column("description", sa.Text))
metrics = sa.table("sustainability_metrics", sa.column("provider_id", sa.Integer),
    sa.column("metric_name", sa.String), sa.column("metric_value", sa.Text),
    sa.column("unit", sa.String), sa.column("source_url", sa.Text),
    sa.column("source_date", sa.Date), sa.column("notes", sa.Text))


def metric_values(row):
    slug, name, value, unit, url, notes = row
    return dict(provider_id=sa.select(providers.c.id).where(providers.c.slug == slug).scalar_subquery(),
                metric_name=name, metric_value=value, unit=unit, source_url=url,
                source_date=None, notes=MARKER + notes)


def upgrade():
    # Refuse a provenance collision rather than claiming ownership of existing rows.
    if op.get_context().as_sql:
        op.execute(sa.text("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM sustainability_metrics
                       WHERE starts_with(notes, 'TopGreenCloud curated reference 0003: ')) THEN
                RAISE EXCEPTION 'Reference migration 0003 provenance marker already exists';
            END IF;
        END $$;"""))
    elif op.get_bind().scalar(sa.select(sa.exists().where(metrics.c.notes.startswith(MARKER)))):
        raise RuntimeError("Reference migration 0003 provenance marker already exists")
    for slug, name, url, description in PROVIDERS:
        values = dict(slug=slug, name=name, website_url=url, description=description)
        op.execute(providers.update().where(providers.c.slug == slug).values(**values))
        op.execute(providers.insert().from_select(list(values), sa.select(
            *(sa.literal(value) for value in values.values())
        ).where(~sa.exists().where(providers.c.slug == slug))))
    for row in METRICS:
        op.execute(metrics.insert().values(**metric_values(row)))


def downgrade():
    # Retain providers: they may predate this migration or own user-data references.
    # Retain edited metrics too; only unchanged, migration-marked rows are ours.
    for row in METRICS:
        values = metric_values(row)
        op.execute(metrics.delete().where(sa.and_(
            *(metrics.c[key] == value for key, value in values.items())
        )))
