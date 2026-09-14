import secrets
import unittest
from io import BytesIO
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

with patch.dict("os.environ", {"DATABASE_URL": "postgresql+psycopg://localhost/topgreencloud"}):
    from app import bills, config
    from app.db import get_db
    from app.main import app
    from app.models import User


class BillTests(unittest.TestCase):
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
        credentials = {"email": "bill-test@example.com", "password": "private test passphrase"}
        self.assertEqual(self.client.post("/auth/register", json=credentials).status_code, 201)
        self.token = self.client.post("/auth/login", json=credentials).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def upload(self, data=b"service,cost\nExample,4.25\n", name="bill.csv", mime="text/csv"):
        return self.client.post("/bills/upload", files={"file": (name, data, mime)}, headers=self.headers)

    def pdf(self, text="Amazon Web Services\nService: EC2\nUsage quantity: 20\nUsage unit: hours"):
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 10 Tf 10 280 Td 12 TL " + " ".join(
            f"({line}) Tj T*" for line in text.splitlines()) + " ET").encode())
        page[NameObject("/Contents")] = stream
        output = BytesIO()
        writer.write(output)
        return output.getvalue()

    def test_authentication_required(self):
        self.assertEqual(self.client.post("/bills/upload", files={"file": ("bill.csv", b"service,cost\na,1")}).status_code, 401)

    def test_csv_safe_response_and_filename(self):
        response = self.upload(name="../../bill.csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["filename"], "bill.csv")
        self.assertIsNone(response.json()["provider"])
        self.assertEqual(response.json()["items"][0]["cost"], "4.25")
        self.assertIsNone(response.json()["items"][0]["region"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        for secret in (self.token, "password_hash", "private test passphrase"):
            self.assertNotIn(secret, response.text)

    def test_csv_providers(self):
        for provider, data in (
            ("aws", b"lineItem/ProductCode,lineItem/UsageAmount,pricing/unit\nEC2,20,hours\n"),
            ("azure", b"MeterCategory,UnitOfMeasure,Quantity\nVirtual Machines,hours,20\n"),
            ("gcp", b"service.description,usage.amount,usage.unit\nCompute Engine,20,hours\n"),
        ):
            with self.subTest(provider=provider):
                response = self.upload(data)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["provider"], provider)
                self.assertEqual(response.json()["items"][0]["usage_quantity"], "20")

    def test_bad_files(self):
        for data, name, mime, status in (
            (b"hello", "bill.exe", "text/plain", 415),
            (b"service,cost\na,1", "bill.csv", "image/png", 415),
            (b"", "bill.csv", "text/csv", 422),
            (b'service,cost\n"unclosed,1', "bill.csv", "text/csv", 422),
            (b"service,cost\na,1,extra", "bill.csv", "text/csv", 422),
            (b"service,cost\n\xff,1", "bill.csv", "text/csv", 422),
            (b"service,cost\na,\x00", "bill.csv", "text/csv", 422),
            (b"not a PDF", "bill.pdf", "application/pdf", 422),
            (b"%PDF-1.4\nbroken", "bill.pdf", "application/pdf", 422),
        ):
            with self.subTest(name=name, data=data):
                response = self.upload(data, name, mime)
                self.assertEqual(response.status_code, status)
                self.assertNotIn("Traceback", response.text)

    def test_size_and_row_limits(self):
        with patch.object(bills, "MAX_FILE_SIZE", 64):
            self.assertEqual(self.upload(b"x" * 65).status_code, 413)
            self.assertEqual(self.upload(b"x" * 70000).status_code, 413)
        with patch.object(bills, "MAX_ROWS", 1):
            self.assertEqual(self.upload(b"service,cost\na,1\nb,2").status_code, 413)

    def test_invalid_numbers_are_not_invented(self):
        response = self.upload(b"service,cost,usage quantity\na,NaN,unknown")
        self.assertIsNone(response.json()["items"][0]["cost"])
        self.assertIsNone(response.json()["items"][0]["usage_quantity"])
        self.assertTrue(response.json()["warnings"])

    def test_unrelated_sensitive_columns_are_omitted(self):
        response = self.upload(b"service,cost,password,token\nExample,1,hidden-password,hidden-token")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("hidden-password", response.text)
        self.assertNotIn("hidden-token", response.text)

    def test_ambiguous_provider_remains_unknown(self):
        response = self.upload(b"lineItem/ProductCode,lineItem/UsageAmount,MeterCategory,UnitOfMeasure\nExample,1,Example,hours")
        self.assertIsNone(response.json()["provider"])

    def test_pdf(self):
        response = self.upload(self.pdf(), "bill.pdf", "application/pdf")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["provider"], "aws")
        self.assertEqual(response.json()["items"][0]["service_name"], "EC2")
        self.assertNotIn("Amazon Web Services", response.text)

    def test_pdf_restrictions(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        output = BytesIO()
        writer.write(output)
        self.assertEqual(self.upload(output.getvalue(), "bill.pdf", "application/pdf").status_code, 422)
        writer.encrypt("test-only")
        output = BytesIO()
        writer.write(output)
        self.assertEqual(self.upload(output.getvalue(), "bill.pdf", "application/pdf").status_code, 422)
        with patch.object(bills, "MAX_PAGES", 0):
            self.assertEqual(self.upload(self.pdf(), "bill.pdf", "application/pdf").status_code, 413)
        with patch.object(bills, "MAX_TEXT", 5):
            self.assertEqual(self.upload(self.pdf(), "bill.pdf", "application/pdf").status_code, 413)

    def test_multiple_files_rejected(self):
        response = self.client.post("/bills/upload", files=[("file", ("a.csv", b"a,b")),
            ("file", ("b.csv", b"a,b"))], headers=self.headers)
        self.assertIn(response.status_code, (400, 422))

    def test_health(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "healthy"})
