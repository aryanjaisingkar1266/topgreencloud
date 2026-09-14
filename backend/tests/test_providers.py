import secrets
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
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
