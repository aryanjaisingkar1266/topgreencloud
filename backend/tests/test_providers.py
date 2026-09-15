import secrets
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

with patch.dict("os.environ", {"DATABASE_URL": "postgresql+psycopg://localhost/topgreencloud"}):
    from app import config
    from app.db import get_db
    from app.main import app
    from app.models import CloudProvider, SustainabilityMetric


class ProviderTests(unittest.TestCase):
    def setUp(self):
        settings = patch.object(config, "JWT_SECRET", secrets.token_urlsafe(48))
        settings.start()
        self.addCleanup(settings.stop)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        CloudProvider.__table__.create(self.engine)
        SustainabilityMetric.__table__.create(self.engine)
        self.addCleanup(self.engine.dispose)

        def database():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = database
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def seed(self):
        # Synthetic records only, never claims about real cloud providers.
        with Session(self.engine) as session:
            for name in ("Delta", "Alpha", "Charlie", "Bravo"):
                provider = CloudProvider(name=f"Test {name}", slug=name.lower())
                session.add(provider)
                session.flush()
                if name == "Alpha":
                    session.add(SustainabilityMetric(provider_id=provider.id,
                        metric_name="test-text", metric_value="synthetic nonnumeric value",
                        source_url="https://example.invalid/test", notes="Test fixture only"))
            session.commit()

    def reference_migration(self):
        path = Path(__file__).resolve().parents[1] / "alembic/versions/0003_verified_sustainability_data.py"
        spec = spec_from_file_location("verified_reference_data", path)
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)
        return migration

    def apply_reference(self, migration, direction="upgrade"):
        with self.engine.begin() as connection:
            with patch.object(migration, "op", Operations(MigrationContext.configure(connection))):
                getattr(migration, direction)()

    def test_reference_data_and_public_api_provenance(self):
        migration = self.reference_migration()
        self.apply_reference(migration)
        response = self.client.get("/providers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p["slug"] for p in response.json()], ["aws", "gcp", "azure"])
        self.assertEqual(len({p["id"] for p in response.json()}), 3)
        allowed_hosts = {"aws.amazon.com", "sustainability.aboutamazon.com",
                         "www.microsoft.com", "datacenters.microsoft.com", "sustainability.google"}
        for slug in ("aws", "azure", "gcp"):
            detail = self.client.get(f"/providers/{slug}")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(len(detail.json()["metrics"]), 3)
            expected = [migration.metric_values(row) for row in migration.METRICS if row[0] == slug]
            for metric in detail.json()["metrics"]:
                url = urlsplit(metric["source_url"])
                self.assertEqual(url.scheme, "https")
                self.assertIn(url.hostname, allowed_hosts)
                self.assertIsNone(url.username)
                self.assertIsNone(metric["source_date"])
                self.assertIn("2025" if slug != "azure" else "FY25", metric["notes"])
                self.assertIn(metric, [{key: value for key, value in item.items() if key != "provider_id"}
                                       for item in expected])
                self.assertNotIn("score", metric["metric_name"].lower())
        comparison = self.client.get("/providers/compare", params={"providers": "gcp,aws,azure"})
        self.assertEqual(comparison.status_code, 200)
        self.assertEqual([p["slug"] for p in comparison.json()], ["gcp", "aws", "azure"])
        self.assertTrue(all(metric["source_url"] for p in comparison.json() for metric in p["metrics"]))

    def test_reference_upgrade_reuses_provider_and_downgrade_preserves_unrelated_data(self):
        migration = self.reference_migration()
        with Session(self.engine) as session:
            provider = CloudProvider(id=42, name="Existing AWS", slug="aws", description="Old description")
            session.add(provider)
            session.flush()
            row = next(row for row in migration.METRICS if row[0] == "aws")
            # Same factual payload, but independently managed: downgrade must retain it.
            session.add(SustainabilityMetric(provider_id=provider.id, metric_name=row[1],
                metric_value=row[2], unit=row[3], source_url=row[4], notes=row[5]))
            session.commit()
        self.apply_reference(migration)
        self.assertEqual(self.client.get("/providers/aws").json()["id"], 42)
        self.assertEqual(self.client.get("/providers/aws").json()["name"], "AWS")
        self.assertEqual(len(self.client.get("/providers/aws").json()["metrics"]), 4)
        with Session(self.engine) as session:
            edited = session.scalar(select(SustainabilityMetric).where(
                SustainabilityMetric.notes.startswith(migration.MARKER)))
            edited.metric_value = "Independently reviewed edit"
            session.commit()
        self.apply_reference(migration, "downgrade")
        self.assertEqual(len(self.client.get("/providers").json()), 3)
        with Session(self.engine) as session:
            remaining = session.scalars(select(SustainabilityMetric)).all()
            self.assertEqual(len(remaining), 2)
            self.assertEqual({item.metric_value for item in remaining}, {row[2], "Independently reviewed edit"})

    def test_reference_provenance_collision_stops_before_writes(self):
        migration = self.reference_migration()
        with Session(self.engine) as session:
            provider = CloudProvider(name="Keep unchanged", slug="aws")
            session.add(provider)
            session.flush()
            session.add(SustainabilityMetric(provider_id=provider.id, metric_name="existing",
                metric_value="existing", source_url="https://example.invalid/test",
                notes=migration.MARKER + "preexisting marker"))
            session.commit()
        with self.assertRaisesRegex(RuntimeError, "provenance marker already exists"):
            self.apply_reference(migration)
        self.assertEqual(self.client.get("/providers/aws").json()["name"], "Keep unchanged")
        self.assertEqual(len(self.client.get("/providers").json()), 1)

    def test_reference_offline_sql_is_data_only(self):
        migration = self.reference_migration()
        for direction in ("upgrade", "downgrade"):
            output = StringIO()
            context = MigrationContext.configure(url="postgresql+psycopg://localhost/topgreencloud",
                opts={"as_sql": True, "literal_binds": True, "output_buffer": output})
            with patch.object(migration, "op", Operations(context)):
                getattr(migration, direction)()
            sql = output.getvalue()
            self.assertNotIn("CREATE TABLE", sql)
            self.assertNotIn("ALTER TABLE", sql)
            self.assertNotIn("DROP TABLE", sql)
            for private_table in ("users", "uploaded_bills", "carbon_analyses", "carbon_analysis_items"):
                self.assertNotIn(private_table, sql)
            if direction == "upgrade":
                self.assertEqual(sql.count("INSERT INTO sustainability_metrics"), 9)
                self.assertEqual(sql.count("INSERT INTO cloud_providers"), 3)
                self.assertIn("RAISE EXCEPTION", sql)
                self.assertNotIn("%%", sql)
            else:
                self.assertEqual(sql.count("DELETE FROM sustainability_metrics"), 9)
                self.assertNotIn("DELETE FROM cloud_providers", sql)
                self.assertEqual(sql.count("sustainability_metrics.notes ="), 9)

    def test_empty_database_is_not_seeded(self):
        response = self.client.get("/providers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_listing_safe_fields_and_sort(self):
        self.seed()
        response = self.client.get("/providers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p["slug"] for p in response.json()], ["alpha", "bravo", "charlie", "delta"])
        self.assertEqual(set(response.json()[0]), {"id", "name", "slug", "website_url", "description"})
        reverse = self.client.get("/providers", params={"sort": "-name"}).json()
        self.assertEqual([p["slug"] for p in reverse], ["delta", "charlie", "bravo", "alpha"])

    def test_search(self):
        self.seed()
        for search in ("ALPHA", " Test Alpha ", "alph"):
            with self.subTest(search=search):
                response = self.client.get("/providers", params={"search": search})
                self.assertEqual([p["slug"] for p in response.json()], ["alpha"])
        for literal in ("%", "_", "' OR 1=1 --"):
            self.assertEqual(self.client.get("/providers", params={"search": literal}).json(), [])

    def test_invalid_list_parameters(self):
        for params in ({"search": ""}, {"search": "  "}, {"search": "x" * 121}, {"sort": "password_hash"}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/providers", params=params).status_code, 422)

    def test_detail_and_faithful_metrics(self):
        self.seed()
        response = self.client.get("/providers/alpha")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["metrics"], [{
            "metric_name": "test-text", "metric_value": "synthetic nonnumeric value",
            "unit": None, "source_url": "https://example.invalid/test", "source_date": None,
            "notes": "Test fixture only",
        }])
        self.assertEqual(self.client.get("/providers/bravo").json()["metrics"], [])

    def test_unknown_detail(self):
        self.assertEqual(self.client.get("/providers/unknown").status_code, 404)

    def test_public_comparison_and_query_count(self):
        self.seed()
        for slugs in ("bravo,alpha", "delta,charlie,alpha,bravo"):
            queries = []
            def record(conn, cursor, statement, parameters, context, executemany):
                queries.append(statement)
            event.listen(self.engine, "before_cursor_execute", record)
            try:
                response = self.client.get("/providers/compare", params={"providers": slugs})
            finally:
                event.remove(self.engine, "before_cursor_execute", record)
            self.assertEqual(response.status_code, 200)
            self.assertEqual([p["slug"] for p in response.json()], slugs.split(","))
            self.assertEqual(len(queries), 2)
            self.assertEqual(next(p for p in response.json() if p["slug"] == "alpha")["metrics"][0]
                             ["metric_value"], "synthetic nonnumeric value")

    def test_invalid_comparisons(self):
        self.seed()
        for value in ("", "alpha", "alpha,bravo,charlie,delta,extra", "alpha,alpha",
                      "alpha, alpha", "alpha,", ",bravo", "alpha,   "):
            with self.subTest(value=value):
                self.assertEqual(self.client.get("/providers/compare", params={"providers": value}).status_code, 422)
        self.assertEqual(self.client.get("/providers/compare").status_code, 422)

    def test_unknown_comparison(self):
        self.seed()
        response = self.client.get("/providers/compare", params={"providers": "alpha,unknown"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "One or more providers were not found"})

    def test_health_and_startup(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "healthy"})
