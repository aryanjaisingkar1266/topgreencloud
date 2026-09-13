import io
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import ForeignKeyConstraint, Numeric
from sqlalchemy.orm import configure_mappers

# Offline checks never connect to a database or consume real credentials.
with patch.dict("os.environ", {"DATABASE_URL": "postgresql+psycopg://localhost/topgreencloud"}):
    from app import models


class SchemaTests(unittest.TestCase):
    def test_tables_and_keys(self):
        tables = models.Base.metadata.tables
        self.assertEqual(set(tables), {
            "users", "cloud_providers", "sustainability_metrics",
            "uploaded_bills", "carbon_analyses", "carbon_analysis_items",
        })
        for table in tables.values():
            self.assertEqual(list(table.primary_key.columns.keys()), ["id"])
            self.assertTrue(table.c.created_at.type.timezone)
        self.assertTrue(next(iter(tables["users"].indexes)).unique)

    def test_relationships_and_bill_ownership(self):
        configure_mappers()
        self.assertIs(models.UploadedBill.user.property.mapper.class_, models.User)
        self.assertIs(models.CarbonAnalysis.bill.property.mapper.class_, models.UploadedBill)
        self.assertIs(models.CarbonAnalysisItem.analysis.property.mapper.class_, models.CarbonAnalysis)
        owner_key = next(c for c in models.CarbonAnalysis.__table__.constraints
                         if isinstance(c, ForeignKeyConstraint) and c.name == "fk_carbon_analyses_bill_owner")
        self.assertEqual(list(owner_key.column_keys), ["bill_id", "user_id"])
        self.assertEqual([e.target_fullname for e in owner_key.elements],
                         ["uploaded_bills.id", "uploaded_bills.user_id"])
        self.assertEqual(owner_key.ondelete, "CASCADE")

    def test_estimates_are_decimal_and_unknown_by_default(self):
        for column in (models.CarbonAnalysis.__table__.c.total_co2e,
                       models.CarbonAnalysisItem.__table__.c.estimated_co2e,
                       models.CarbonAnalysisItem.__table__.c.usage_quantity):
            self.assertIsInstance(column.type, Numeric)
            self.assertTrue(column.nullable)
            self.assertIsNone(column.server_default)

    def test_migration_upgrade_and_downgrade_render(self):
        output = io.StringIO()
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
        command.upgrade(config, "head", sql=True)
        sql = output.getvalue()
        for table in models.Base.metadata.tables:
            self.assertIn(f"CREATE TABLE {table}", sql)
        self.assertEqual(sql.count("CREATE TABLE "), 7)  # Six tables plus Alembic's version table.
        self.assertIn("FOREIGN KEY(bill_id, user_id)", sql)
        self.assertIn("CREATE UNIQUE INDEX uq_users_email_lower", sql)
        output.seek(0)
        output.truncate()
        command.downgrade(config, "0001:base", sql=True)
        for table in models.Base.metadata.tables:
            self.assertIn(f"DROP TABLE {table}", output.getvalue())
        self.assertEqual(output.getvalue().count("DROP TABLE "), 6)
