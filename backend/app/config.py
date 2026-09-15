import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL.startswith("postgresql+psycopg://"):
    raise RuntimeError("Set DATABASE_URL to a postgresql+psycopg:// connection URL.")

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
GCS_BUCKET = os.environ.get("GCS_BUCKET", "").strip()


def validate_auth_config():
    if len(JWT_SECRET.encode()) < 32 or JWT_SECRET == "replace-with-secure-random-secret":
        raise RuntimeError("JWT_SECRET must be a secure random secret of at least 32 bytes.")
    if JWT_ALGORITHM != "HS256":
        raise RuntimeError("Only HS256 is supported.")
    if not 1 <= ACCESS_TOKEN_EXPIRE_MINUTES <= 60:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES must be between 1 and 60.")
