import re
from uuid import uuid4

from google.api_core.exceptions import NotFound
from google.cloud import storage

from app import config


class StorageError(Exception):
    pass


def object_key(user_id: int, kind: str) -> str:
    if type(user_id) is not int or user_id <= 0 or kind not in ("csv", "pdf"):
        raise ValueError("Invalid storage identifiers")
    return f"bills/{user_id}/{uuid4().hex}.{kind}"


def upload_bill(key: str, data: bytes, kind: str) -> None:
    if kind not in ("csv", "pdf") or not re.fullmatch(r"bills/[1-9][0-9]*/[0-9a-f]{32}\." + kind, key):
        raise StorageError("Invalid storage identifiers")
    try:
        # Client() uses ADC, including attached Google Cloud identities.
        with storage.Client() as client:
            bucket = client.get_bucket(config.GCS_BUCKET, timeout=10, retry=None)
            if (bucket.iam_configuration.public_access_prevention != "enforced" or
                    not bucket.iam_configuration.uniform_bucket_level_access_enabled):
                raise StorageError("Private bucket security controls are required")
            blob = bucket.blob(key)
            blob.cache_control = "no-store"
            blob.upload_from_string(data, content_type="text/csv" if kind == "csv" else "application/pdf",
                                    if_generation_match=0, timeout=20, retry=None)
    except Exception:
        raise StorageError("Bill storage is temporarily unavailable") from None


def delete_bill(key: str) -> None:
    if not config.GCS_BUCKET or not re.fullmatch(r"bills/[1-9][0-9]*/[0-9a-f]{32}\.(csv|pdf)", key):
        raise StorageError("Bill storage is unavailable")
    try:
        with storage.Client() as client:
            client.bucket(config.GCS_BUCKET).blob(key).delete(timeout=10, retry=None)
    except NotFound:
        pass
    except Exception:
        raise StorageError("Bill storage is temporarily unavailable") from None
