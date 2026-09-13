import secrets
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

# Use the existing User mapping in memory; these are not PostgreSQL migration tests.
with patch.dict("os.environ", {"DATABASE_URL": "postgresql+psycopg://localhost/topgreencloud"}):
    from app import auth, config
    from app.db import get_db
    from app.main import app
    from app.models import User


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.settings = patch.object(config, "JWT_SECRET", secrets.token_urlsafe(48))
        self.settings.start()
        self.addCleanup(self.settings.stop)
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
        self.data = {"email": "person@example.com", "password": "a long test passphrase"}

    def register(self):
        response = self.client.post("/auth/register", json=self.data)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def login(self):
        response = self.client.post("/auth/login", json=self.data)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["token_type"], "bearer")
        return response.json()["access_token"]

    def me(self, token):
        return self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    def test_registration_and_password_hash(self):
        self.data["email"] = " Person@EXAMPLE.COM "
        user = self.register()
        self.assertEqual(user["email"], "person@example.com")
        self.assertFalse(user["is_admin"])
        with Session(self.engine) as session:
            stored = session.scalar(select(User))
            self.assertNotEqual(stored.password_hash, self.data["password"])
            self.assertTrue(stored.password_hash.startswith("$argon2id$"))
            self.assertTrue(auth.verify_password(self.data["password"], stored.password_hash))
        self.assertNotIn("password_hash", user)

    def test_duplicate_registration(self):
        self.register()
        self.data["email"] = "PERSON@example.com"
        self.assertEqual(self.client.post("/auth/register", json=self.data).status_code, 409)

    def test_valid_login_and_me(self):
        user = self.register()
        token = self.login()
        claims = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
        self.assertEqual(claims["sub"], str(user["id"]))
        self.assertLessEqual(claims["exp"] - datetime.now(timezone.utc).timestamp(), 1800)
        response = self.me(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"id", "email", "is_active", "is_admin", "created_at"})
        self.assertEqual(response.json()["id"], user["id"])
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_incorrect_and_unknown_credentials(self):
        self.register()
        wrong = self.client.post("/auth/login", json={**self.data, "password": "wrong password"})
        unknown = self.client.post("/auth/login", json={**self.data, "email": "unknown@example.com"})
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(wrong.json(), unknown.json())

    def test_inactive_user(self):
        self.register()
        token = self.login()
        with Session(self.engine) as session:
            session.scalar(select(User)).is_active = False
            session.commit()
        self.assertEqual(self.client.post("/auth/login", json=self.data).status_code, 401)
        self.assertEqual(self.me(token).status_code, 401)

    def test_invalid_malformed_expired_and_missing_tokens(self):
        self.register()
        for claims, key, algorithm in (
            ({"sub": "1", "exp": 1}, config.JWT_SECRET, "HS256"),
            ({"sub": "1", "exp": 4102444800}, secrets.token_urlsafe(48), "HS256"),
            ({"sub": "1"}, config.JWT_SECRET, "HS256"),
            ({"exp": 4102444800}, config.JWT_SECRET, "HS256"),
            ({"sub": "not-an-id", "exp": 4102444800}, config.JWT_SECRET, "HS256"),
            ({"sub": "999999999999999999", "exp": 4102444800}, config.JWT_SECRET, "HS256"),
            ({"sub": "1", "exp": 4102444800}, "", "none"),
        ):
            with self.subTest(claims=claims, algorithm=algorithm):
                self.assertEqual(self.me(jwt.encode(claims, key, algorithm=algorithm)).status_code, 401)
        self.assertEqual(self.me("malformed.token").status_code, 401)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_public_registration_cannot_create_admin(self):
        response = self.client.post("/auth/register", json={**self.data, "is_admin": True})
        self.assertEqual(response.status_code, 422)
        self.assertFalse(self.register()["is_admin"])

    def test_validation_does_not_echo_password(self):
        for values in ({**self.data, "password": "tiny123"}, {**self.data, "email": "bad-email"}):
            response = self.client.post("/auth/register", json=values)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(values["password"], response.text)
            self.assertNotIn("password_hash", response.text)

    def test_health(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "healthy"})

    def test_unknown_user_token(self):
        self.assertEqual(self.me(auth.create_token(123)).status_code, 401)

    def test_unsafe_configuration_rejected(self):
        for name, value in (("JWT_SECRET", "short"), ("JWT_ALGORITHM", "none"), ("ACCESS_TOKEN_EXPIRE_MINUTES", 0)):
            with self.subTest(name=name), patch.object(config, name, value):
                with self.assertRaises(RuntimeError):
                    config.validate_auth_config()
