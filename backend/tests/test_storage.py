import secrets
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.api_core.exceptions import NotFound
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

with patch.dict("os.environ", {"DATABASE_URL": "postgresql+psycopg://localhost/topgreencloud"}):
    from app import config, storage
    from app.db import Base, get_db
    from app.main import app
    from app.models import CloudProvider, UploadedBill


class StorageTests(unittest.TestCase):
    def setUp(self):
        for setting, value in (("JWT_SECRET", secrets.token_urlsafe(48)), ("GCS_BUCKET", "test-private-bucket")):
            patcher = patch.object(config, setting, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
        credentials = {"email": "storage@example.com", "password": "test-only passphrase"}
        self.user_id = self.client.post("/auth/register", json=credentials).json()["id"]
        self.token = self.client.post("/auth/login", json=credentials).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
        mock = patch.object(storage.storage, "Client")
        self.client_factory = mock.start()
        self.addCleanup(mock.stop)
        self.gcs = self.client_factory.return_value.__enter__.return_value
        self.bucket = self.gcs.get_bucket.return_value
        self.bucket.iam_configuration.public_access_prevention = "enforced"
        self.bucket.iam_configuration.uniform_bucket_level_access_enabled = True

    def upload(self, data=b"service,cost\nSynthetic,1", filename="../../private.csv"):
        return self.client.post("/bills/upload", files={"file": (filename, data, "text/csv")}, headers=self.headers)

    def rows(self):
        with Session(self.engine) as db:
            return db.scalars(select(UploadedBill)).all()

    def test_safe_keys(self):
        key = storage.object_key(1, "csv")
        self.assertRegex(key, r"^bills/1/[0-9a-f]{32}\.csv$")
        self.assertNotEqual(key, storage.object_key(1, "csv"))
        for user, kind in (("../1", "csv"), (1, "../pdf"), (0, "csv")):
            with self.assertRaises(ValueError):
                storage.object_key(user, kind)

    def test_adc_bucket_content_type_and_metadata(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        row, = self.rows()
        self.assertEqual(row.id, response.json()["bill_id"])
        self.assertEqual(row.user_id, self.user_id)
        self.assertEqual(row.original_filename, "private.csv")
        self.assertEqual(row.status, "parsed")
        self.assertIsNone(row.provider_id)
        self.assertNotIn("private", row.storage_key)
        self.client_factory.assert_called_once_with()
        self.gcs.get_bucket.assert_called_once_with("test-private-bucket", timeout=10, retry=None)
        self.bucket.blob.assert_called_once_with(row.storage_key)
        self.bucket.blob.return_value.upload_from_string.assert_called_once_with(
            b"service,cost\nSynthetic,1", content_type="text/csv", if_generation_match=0, timeout=20, retry=None)
        for private in (row.storage_key, self.token, "password_hash"):
            self.assertNotIn(private, response.text)

    def test_pdf_content_type(self):
        storage.upload_bill(storage.object_key(1, "pdf"), b"validated-test-bytes", "pdf")
        self.assertEqual(self.bucket.blob.return_value.upload_from_string.call_args.kwargs["content_type"], "application/pdf")

    def test_provider_match(self):
        with Session(self.engine) as db:
            provider = CloudProvider(name="Synthetic AWS fixture", slug="aws")
            db.add(provider)
            db.flush()
            provider_id = provider.id
            db.commit()
        self.assertEqual(self.upload(b"lineItem/ProductCode,lineItem/UsageAmount\nSynthetic,1").status_code, 200)
        self.assertEqual(self.rows()[0].provider_id, provider_id)

    def test_disabled_is_stateless(self):
        with patch.object(config, "GCS_BUCKET", ""):
            response = self.upload()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["bill_id"])
        self.assertEqual(self.rows(), [])
        self.client_factory.assert_not_called()

    def test_upload_failure(self):
        self.bucket.blob.return_value.upload_from_string.side_effect = RuntimeError("sensitive internal error")
        response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("sensitive", response.text)
        self.assertEqual(self.rows(), [])

    def test_insecure_bucket_rejected(self):
        self.bucket.iam_configuration.public_access_prevention = "inherited"
        self.assertEqual(self.upload().status_code, 503)
        self.bucket.blob.assert_not_called()
        self.assertEqual(self.rows(), [])

    def test_database_failure_cleans_object(self):
        with patch.object(Session, "commit", side_effect=SQLAlchemyError("private SQL")):
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.gcs.bucket.return_value.blob.return_value.delete.assert_called_once()
        self.assertEqual(self.rows(), [])
        self.assertNotIn("private SQL", response.text)

    def test_delete_ownership_and_missing_object(self):
        bill_id = self.upload().json()["bill_id"]
        credentials = {"email": "other@example.com", "password": "another test passphrase"}
        self.client.post("/auth/register", json=credentials)
        token = self.client.post("/auth/login", json=credentials).json()["access_token"]
        self.assertEqual(self.client.delete(f"/bills/{bill_id}", headers={"Authorization": f"Bearer {token}"}).status_code, 404)
        self.gcs.bucket.assert_not_called()
        self.assertEqual(self.client.delete(f"/bills/{bill_id}").status_code, 401)
        self.gcs.bucket.return_value.blob.return_value.delete.side_effect = NotFound("missing")
        self.assertEqual(self.client.delete(f"/bills/{bill_id}", headers=self.headers).status_code, 204)
        self.assertEqual(self.rows(), [])

    def test_delete_failure_keeps_metadata(self):
        bill_id = self.upload().json()["bill_id"]
        self.gcs.bucket.return_value.blob.return_value.delete.side_effect = RuntimeError("private error")
        self.assertEqual(self.client.delete(f"/bills/{bill_id}", headers=self.headers).status_code, 503)
        self.assertEqual(len(self.rows()), 1)

    def test_owned_delete_and_database_retry(self):
        bill_id = self.upload().json()["bill_id"]
        key = self.rows()[0].storage_key
        with patch.object(Session, "commit", side_effect=SQLAlchemyError("private SQL")):
            self.assertEqual(self.client.delete(f"/bills/{bill_id}", headers=self.headers).status_code, 503)
        self.assertEqual(len(self.rows()), 1)
        self.gcs.bucket.return_value.blob.assert_called_with(key)
        self.assertEqual(self.client.delete(f"/bills/{bill_id}", headers=self.headers).status_code, 204)
        self.assertEqual(self.rows(), [])

    def test_cleanup_failure_is_safe(self):
        self.gcs.bucket.return_value.blob.return_value.delete.side_effect = RuntimeError("private cleanup error")
        with patch.object(Session, "commit", side_effect=SQLAlchemyError("private SQL")):
            with self.assertLogs("app.bills", level="WARNING") as logs:
                response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)
        self.assertNotIn("private", str(logs.output))
        self.assertEqual(self.rows(), [])

    def test_delete_preflight(self):
        response = self.client.options("/bills/1", headers={"Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "DELETE", "Access-Control-Request-Headers": "Authorization"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:3000")

    def test_invalid_upload_never_stored(self):
        self.assertEqual(self.upload(b"malformed").status_code, 422)
        self.client_factory.assert_not_called()

    def test_postgresql_and_health(self):
        self.assertTrue(config.DATABASE_URL.startswith("postgresql+psycopg://"))
        self.assertEqual(self.client.get("/health").json(), {"status": "healthy"})
