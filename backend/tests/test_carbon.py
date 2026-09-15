import secrets
import unittest
import csv
import sqlite3
from decimal import Decimal
from datetime import date
from io import StringIO
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

with patch.dict("os.environ", {"DATABASE_URL": "postgresql+psycopg://localhost/topgreencloud"}):
    from app import carbon, config
    from app.db import Base, get_db
    from app.main import app
    from app.models import CarbonAnalysis, CarbonAnalysisItem, UploadedBill, User


class CarbonTests(unittest.TestCase):
    def setUp(self):
        settings = patch.object(config, "JWT_SECRET", secrets.token_urlsafe(48))
        settings.start()
        self.addCleanup(settings.stop)
        sqlite3.register_adapter(Decimal, lambda value: str(value).encode())
        sqlite3.register_converter("NUMERIC", lambda value: Decimal(value.decode()))
        sqlite3.register_converter("DATE", lambda value: value.decode())
        self.engine = create_engine("sqlite://", connect_args={
            "check_same_thread": False, "detect_types": sqlite3.PARSE_DECLTYPES,
        }, poolclass=StaticPool)
        self.engine.dialect.supports_native_decimal = True
        Base.metadata.create_all(self.engine)
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
        with Session(self.engine) as session:
            self.user_id = session.scalar(select(User.id).where(User.email == credentials["email"]))
        self.item = dict(provider="aws", service_name="synthetic-service", service_category="compute",
                         region="synthetic-region", usage_quantity="10", usage_unit="synthetic-unit")
        # This arithmetic fixture is not a real environmental factor.
        self.factor = carbon.Coefficient(**{k: v for k, v in self.item.items() if k != "usage_quantity"},
            kg_co2e_per_unit="0.5", source=carbon.Source(name="Synthetic test fixture",
            url="https://example.invalid/synthetic-test", methodology_version="test-only"))

    def calculate(self, items, factors=None):
        with patch.object(carbon, "COEFFICIENTS", (self.factor,) if factors is None else factors):
            return self.client.post("/carbon/calculate", json={"items": items}, headers=self.headers)

    def stored_bill(self, filename="bill.csv", user_id=None):
        with Session(self.engine) as session:
            bill = UploadedBill(user_id=user_id or self.user_id, original_filename=filename,
                                storage_key=f"bills/{user_id or self.user_id}/{secrets.token_hex(16)}.csv",
                                file_type="csv", file_size=10, status="parsed")
            session.add(bill); session.commit()
            return bill.id

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

    def test_persisted_exact_snapshot_and_history(self):
        bill_id = self.stored_bill("=audit.csv")
        long_name = "+" + "s" * 999
        item = self.item | {"service_name": long_name, "usage_quantity": "0.123456789"}
        factor = self.factor.model_copy(update={
            "service_name": long_name, "kg_co2e_per_unit": Decimal("0.987654321"),
            "source": self.factor.source.model_copy(update={
                "name": "=" + "S" * 999, "source_date": date(2026, 1, 2),
            }),
        })
        with patch.object(carbon, "COEFFICIENTS", (factor,)):
            response = self.client.post("/carbon/calculate", json={
                "bill_id": bill_id, "provider": "aws", "items": [item, item],
            }, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        expected_item = Decimal("0.121932631112635269")
        expected_total = Decimal("0.243865262225270538")
        self.assertEqual(Decimal(response.json()["total_kg_co2e"]), expected_total)
        analysis_id = response.json()["analysis_id"]
        with Session(self.engine) as session:
            analysis = session.get(CarbonAnalysis, analysis_id)
            items = session.query(CarbonAnalysisItem).filter_by(analysis_id=analysis_id).all()
            self.assertEqual(analysis.total_co2e, expected_total)
            self.assertTrue(analysis.complete)
            self.assertEqual(items[0].service_name, long_name)
            self.assertEqual(items[0].estimated_co2e, expected_item)
            self.assertEqual(items[0].kg_co2e_per_unit, Decimal("0.987654321"))
            self.assertEqual(items[0].source_name, "=" + "S" * 999)
            self.assertEqual(items[0].source_url, "https://example.invalid/synthetic-test")
            self.assertEqual(items[0].source_methodology_version, "test-only")
            self.assertEqual(items[0].source_date, date(2026, 1, 2))
        history = self.client.get("/carbon/history", headers=self.headers)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(Decimal(history.json()[0]["total_kg_co2e"]), expected_total)
        exported = self.client.get(f"/carbon/history/{analysis_id}/export.csv", headers=self.headers)
        rows = list(csv.DictReader(StringIO(exported.text)))
        self.assertEqual(rows[0]["analysis_total_kg_co2e"], str(expected_total))
        self.assertEqual(rows[0]["estimated_kg_co2e"], str(expected_item))
        self.assertTrue(rows[0]["bill_filename"].startswith("'="))
        self.assertTrue(rows[0]["service_name"].startswith("'+"))
        self.assertTrue(rows[0]["source_name"].startswith("'="))

    def test_unsupported_snapshot_null_total_and_empty_csv_cells(self):
        bill_id = self.stored_bill()
        response = self.client.post("/carbon/calculate", json={
            "bill_id": bill_id, "items": [{"provider": "unknown", "service_name": None}],
        }, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["total_kg_co2e"])
        self.assertFalse(response.json()["complete"])
        analysis_id = response.json()["analysis_id"]
        with Session(self.engine) as session:
            item = session.query(CarbonAnalysisItem).filter_by(analysis_id=analysis_id).one()
            self.assertIsNone(item.service_name)
            self.assertIsNone(item.estimated_co2e)
            self.assertEqual(item.unsupported_reason, "Unknown or missing provider")
        exported = self.client.get(f"/carbon/history/{analysis_id}/export.csv", headers=self.headers)
        row = next(csv.DictReader(StringIO(exported.text)))
        self.assertEqual(row["analysis_total_kg_co2e"], "")
        self.assertEqual(row["estimated_kg_co2e"], "")
        self.assertEqual(row["kg_co2e_per_unit"], "")

    def test_bill_ownership_history_and_export_are_private(self):
        credentials = {"email": "other-carbon@example.com", "password": "private test passphrase"}
        self.client.post("/auth/register", json=credentials)
        other_token = self.client.post("/auth/login", json=credentials).json()["access_token"]
        with Session(self.engine) as session:
            other_id = session.scalar(select(User.id).where(User.email == credentials["email"]))
        foreign_bill = self.stored_bill(user_id=other_id)
        with patch.object(carbon, "COEFFICIENTS", (self.factor,)):
            foreign = self.client.post("/carbon/calculate", json={
                "bill_id": foreign_bill, "items": [self.item],
            }, headers=self.headers)
            own = self.client.post("/carbon/calculate", json={
                "bill_id": foreign_bill, "items": [self.item],
            }, headers={"Authorization": f"Bearer {other_token}"})
        self.assertEqual(foreign.status_code, 404)
        analysis_id = own.json()["analysis_id"]
        self.assertEqual(self.client.get("/carbon/history", headers=self.headers).json(), [])
        self.assertEqual(self.client.get(f"/carbon/history/{analysis_id}/export.csv", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get("/carbon/history").status_code, 401)

    def test_history_is_newest_first(self):
        bill_id = self.stored_bill()
        with patch.object(carbon, "COEFFICIENTS", (self.factor,)):
            first = self.client.post("/carbon/calculate", json={"bill_id": bill_id, "items": [self.item]}, headers=self.headers).json()["analysis_id"]
            second = self.client.post("/carbon/calculate", json={"bill_id": bill_id, "items": [self.item]}, headers=self.headers).json()["analysis_id"]
        self.assertEqual([row["analysis_id"] for row in self.client.get("/carbon/history", headers=self.headers).json()], [second, first])

    def test_persistence_failure_is_not_reported_as_saved(self):
        bill_id = self.stored_bill()
        with patch.object(carbon, "persist_analysis", side_effect=carbon.SQLAlchemyError()):
            response = self.client.post("/carbon/calculate", json={
                "bill_id": bill_id, "items": [self.item],
            }, headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Analysis could not be saved. Please retry."})
        self.assertNotIn("analysis_id", response.text)

    def test_health(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "healthy"})
