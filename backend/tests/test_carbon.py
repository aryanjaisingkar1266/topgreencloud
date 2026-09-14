import secrets
import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

with patch.dict("os.environ", {"DATABASE_URL": "postgresql+psycopg://localhost/topgreencloud"}):
    from app import carbon, config
    from app.db import get_db
    from app.main import app
    from app.models import User


class CarbonTests(unittest.TestCase):
    def setUp(self):
        settings = patch.object(config, "JWT_SECRET", secrets.token_urlsafe(48))
        settings.start()
        self.addCleanup(settings.stop)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        User.__table__.create(self.engine)
        self.addCleanup(self.engine.dispose)

        def database():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = database
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        credentials = {"email": "carbon-test@example.com", "password": "private test passphrase"}
        self.assertEqual(self.client.post("/auth/register", json=credentials).status_code, 201)
        self.token = self.client.post("/auth/login", json=credentials).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.item = dict(provider="aws", service_name="synthetic-service", service_category="compute",
                         region="synthetic-region", usage_quantity="10", usage_unit="synthetic-unit")
        # This arithmetic fixture is not a real environmental factor.
        self.factor = carbon.Coefficient(**{k: v for k, v in self.item.items() if k != "usage_quantity"},
            kg_co2e_per_unit="0.5", source=carbon.Source(name="Synthetic test fixture",
            url="https://example.invalid/synthetic-test", methodology_version="test-only"))

    def calculate(self, items, factors=None):
        with patch.object(carbon, "COEFFICIENTS", (self.factor,) if factors is None else factors):
            return self.client.post("/carbon/calculate", json={"items": items}, headers=self.headers)

    def test_authentication_required(self):
        response = self.client.post("/carbon/calculate", json={"items": [self.item]})
        self.assertEqual(response.status_code, 401)

    def test_arithmetic_source_and_safe_response(self):
        response = self.calculate([self.item, self.item])
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(Decimal(result["total_kg_co2e"]), Decimal("10"))
        self.assertTrue(result["complete"])
        self.assertEqual(result["calculated_items"][0]["source"]["methodology_version"], "test-only")
        self.assertEqual(response.headers["cache-control"], "no-store")
        for secret in (self.token, "password_hash", "private test passphrase"):
            self.assertNotIn(secret, response.text)

    def test_empty_production_registry_and_unknown_total(self):
        self.assertEqual(carbon.COEFFICIENTS, ())
        result = self.calculate([self.item], ()).json()
        self.assertIsNone(result["total_kg_co2e"])
        self.assertEqual(result["calculated_items"], [])
        self.assertNotIn("estimated_kg_co2e", result["unsupported_items"][0])
        self.assertFalse(result["complete"])

    def test_partial_results(self):
        result = self.calculate([self.item, self.item | {"usage_unit": "incompatible"}]).json()
        self.assertEqual(Decimal(result["total_kg_co2e"]), Decimal("5"))
        self.assertEqual(len(result["unsupported_items"]), 1)
        self.assertTrue(result["warnings"])
        self.assertFalse(result["complete"])

    def test_exact_applicability_and_missing_values(self):
        for field, value in (("provider", "unknown"), ("provider", None), ("region", "other"),
                             ("service_name", "other"), ("service_category", "other"),
                             ("usage_unit", "other"), ("usage_quantity", None)):
            with self.subTest(field=field):
                result = self.calculate([self.item | {field: value}]).json()
                self.assertIsNone(result["total_kg_co2e"])
                self.assertEqual(len(result["unsupported_items"]), 1)

    def test_invalid_input(self):
        for field, value in (("usage_quantity", "-1"), ("usage_quantity", "NaN"),
                             ("usage_quantity", "Infinity"), ("usage_quantity", "1e1000"),
                             ("region", "  "), ("service_name", "x" * 1001)):
            with self.subTest(field=field, value=value):
                self.assertEqual(self.calculate([self.item | {field: value}]).status_code, 422)
        self.assertEqual(self.calculate([]).status_code, 422)

    def test_zero_and_decimal_precision(self):
        factor = self.factor.model_copy(update={"kg_co2e_per_unit": Decimal("0.2")})
        result = self.calculate([self.item | {"usage_quantity": "0.1"}] * 3, (factor,)).json()
        self.assertEqual(Decimal(result["total_kg_co2e"]), Decimal("0.06"))
        zero = self.calculate([self.item | {"usage_quantity": "0"}]).json()
        self.assertEqual(Decimal(zero["total_kg_co2e"]), Decimal(0))
        self.assertTrue(zero["complete"])

    def test_ambiguous_factors(self):
        result = self.calculate([self.item], (self.factor, self.factor)).json()
        self.assertIsNone(result["total_kg_co2e"])
        self.assertIn("Ambiguous", result["unsupported_items"][0]["reason"])

    def test_source_validation(self):
        with self.assertRaises(ValidationError):
            carbon.Source(name="", url="not-a-url", methodology_version="")

    def test_phase5_shape_and_explicit_unknown_provider(self):
        item = {k: v for k, v in self.item.items() if k != "provider"}
        payload = {"provider": "aws", "filename": "bill.csv", "items": [item | {"cost": "1"}]}
        with patch.object(carbon, "COEFFICIENTS", (self.factor,)):
            response = self.client.post("/carbon/calculate", json=payload, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["complete"])
            payload["items"][0]["provider"] = None
            response = self.client.post("/carbon/calculate", json=payload, headers=self.headers)
            self.assertIsNone(response.json()["total_kg_co2e"])

    def test_no_database_writes(self):
        statements = []
        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.lstrip().upper())
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            self.assertEqual(self.calculate([self.item]).status_code, 200)
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertTrue(statements)
        self.assertTrue(all(statement.startswith("SELECT") for statement in statements))

    def test_health(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "healthy"})
