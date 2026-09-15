import csv
import logging
import re
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from pypdf import PdfReader, apply_configuration
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import config, storage
from app.auth import get_current_user
from app.db import get_db
from app.models import CloudProvider, UploadedBill, User

router = APIRouter(prefix="/bills", tags=["bills"])
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_ROWS = 10000
MAX_PAGES = 50
MAX_TEXT = 2 * 1024 * 1024
# Parser diagnostics may contain fragments of private documents.
for logger_name in ("pypdf", "python_multipart"):
    parser_logger = logging.getLogger(logger_name)
    parser_logger.addHandler(logging.NullHandler())
    parser_logger.propagate = False

ALIASES = {
    "service_name": ("service", "service name", "lineitem/productcode", "product/productname", "servicename", "metercategory", "service.description"),
    "service_category": ("service category", "service_category"),
    "region": ("region", "product/region", "resourcelocation", "location.region"),
    "usage_quantity": ("usage quantity", "usage_quantity", "lineitem/usageamount", "quantity", "usage.quantity", "usage.amount"),
    "usage_unit": ("usage unit", "usage_unit", "pricing/unit", "unitofmeasure", "usage.unit"),
    "cost": ("cost", "lineitem/unblendedcost", "costinbillingcurrency", "pretaxcost"),
    "currency": ("currency", "lineitem/currencycode", "billingcurrency"),
    "billing_period": ("billing period", "billing_period", "bill/billingperiodstartdate", "billingperiodstartdate", "invoice.month"),
}


class Item(BaseModel):
    service_name: str | None = None
    service_category: str | None = None
    region: str | None = None
    usage_quantity: Decimal | None = None
    usage_unit: str | None = None
    cost: Decimal | None = None
    currency: str | None = None
    billing_period: str | None = None


class ParsedBill(BaseModel):
    bill_id: int | None = None
    filename: str
    file_type: Literal["csv", "pdf"]
    provider: Literal["aws", "azure", "gcp"] | None
    row_count: int
    items: list[Item]
    warnings: list[str]


def normalize(values: dict[str, str], warnings: set[str]) -> Item:
    fields = {}
    for field, aliases in ALIASES.items():
        value = next((values[key].strip() for key in aliases if values.get(key, "").strip()), None)
        if value is not None and len(value) > 1000:
            raise HTTPException(422, "Billing field exceeds the supported length")
        if value is not None and field in ("usage_quantity", "cost"):
            try:
                # Ambiguous locale formatting and nonfinite values are not guessed.
                if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
                    raise InvalidOperation
                value = Decimal(value)
            except InvalidOperation:
                value = None
                warnings.add("Some numeric fields were unreadable and left null")
        fields[field] = value
    return Item(**fields)


def csv_bill(data: bytes):
    warnings = set()
    try:
        text = data.decode("utf-8-sig")
        if any(ord(char) < 32 and char not in "\r\n\t" for char in text):
            raise ValueError
        rows = csv.reader(StringIO(text, newline=""), strict=True)
        headers = [value.strip().lower() for value in next(rows)]
        if not 2 <= len(headers) <= 100 or any(not h for h in headers) or len(set(headers)) != len(headers):
            raise ValueError
        if not set(headers).intersection(alias for aliases in ALIASES.values() for alias in aliases):
            raise HTTPException(422, "CSV has no recognized billing columns")
        matches = []
        if {"lineitem/productcode", "lineitem/usageamount"}.issubset(headers):
            matches.append("aws")
        if {"metercategory", "unitofmeasure"}.issubset(headers):
            matches.append("azure")
        if "service.description" in headers and {"usage.amount", "usage.quantity"}.intersection(headers):
            matches.append("gcp")
        provider = matches[0] if len(matches) == 1 else None
        items = []
        for row in rows:
            if not row or not any(value.strip() for value in row):
                continue
            if len(items) >= MAX_ROWS:
                raise HTTPException(413, "CSV exceeds the supported row count")
            if len(row) != len(headers):
                raise ValueError
            items.append(normalize(dict(zip(headers, row)), warnings))
        if not items:
            raise ValueError
    except (UnicodeError, csv.Error, ValueError, StopIteration):
        raise HTTPException(422, "Malformed or unsupported UTF-8 CSV") from None
    if provider is None:
        warnings.add("Provider could not be identified reliably")
    return provider, items, sorted(warnings)


@apply_configuration(zlib_maximum_output_length=MAX_TEXT,
                     lzw_maximum_output_length=MAX_TEXT,
                     run_length_maximum_output_length=MAX_TEXT,
                     array_based_stream_maximum_output_length=MAX_TEXT,
                     page_tree_maximum_entries=1000,
                     xform_maximum_invocations_per_extraction=100)
def pdf_bill(data: bytes):
    if not data.startswith(b"%PDF-"):
        raise HTTPException(422, "Malformed PDF")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise HTTPException(422, "Encrypted PDFs are not supported")
        if len(reader.pages) > MAX_PAGES:
            raise HTTPException(413, "PDF exceeds the supported page count")
        texts = []
        size = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            size += len(text)
            if size > MAX_TEXT:
                raise HTTPException(413, "PDF exceeds the supported text size")
            texts.append(text)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Malformed or unreadable PDF") from None
    text = "\n".join(texts)
    if not text.strip():
        raise HTTPException(422, "PDF has no readable text; scanned PDFs are not supported")
    matches = [provider for provider, name in (
        ("aws", "amazon web services"), ("azure", "microsoft azure"), ("gcp", "google cloud")
    ) if re.search(r"\b" + name + r"\b", text.lower())]
    warnings = {"PDF extraction is partial; only explicit labeled fields are supported"}
    if len(matches) != 1:
        warnings.add("Provider could not be identified reliably")
    # Each explicit Service: line starts an item; unrelated invoice text is omitted.
    items, values = [], {}
    labels = {alias for aliases in ALIASES.values() for alias in aliases}
    for line in text.splitlines():
        label, separator, value = line.partition(":")
        label = label.strip().lower()
        if not separator or label not in labels:
            continue
        if label in ALIASES["service_name"] and values:
            items.append(normalize(values, warnings))
            values = {}
        if label in values:
            warnings.add("Repeated PDF fields could not be associated reliably")
            continue
        values[label] = value.strip()
        if len(items) >= MAX_ROWS:
            raise HTTPException(413, "PDF exceeds the supported item count")
    if values:
        items.append(normalize(values, warnings))
    return matches[0] if len(matches) == 1 else None, items, sorted(warnings)


@router.post("/upload", response_model=ParsedBill)
async def upload(request: Request, response: Response, user: Annotated[User, Depends(get_current_user)],
                 db: Annotated[Session, Depends(get_db)]):
    # Bound the entire body before multipart parsing can spool unbounded input.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_FILE_SIZE + 65536:
            raise HTTPException(413, "Upload exceeds the 10 MiB limit")
        body.extend(chunk)

    async def receive():
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    bounded = Request(request.scope, receive)
    try:
        async with bounded.form(max_files=1, max_fields=0) as form:
            file = form.get("file")
            if len(form.multi_items()) != 1 or not isinstance(file, UploadFile):
                raise HTTPException(422, "Provide exactly one file in the file field")
            filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
            filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:200]
            kind = filename.rsplit(".", 1)[-1].lower()
            if kind not in ("csv", "pdf"):
                raise HTTPException(415, "Only CSV and PDF files are supported")
            allowed = {"csv": {"text/csv", "application/csv", "text/plain", "application/vnd.ms-excel"},
                       "pdf": {"application/pdf"}}
            if (file.content_type or "").split(";")[0].lower() not in allowed[kind]:
                raise HTTPException(415, "Unsupported file content type")
            data = await file.read(MAX_FILE_SIZE + 1)
            if len(data) > MAX_FILE_SIZE:
                raise HTTPException(413, "Upload exceeds the 10 MiB limit")
            if not data or not data.strip():
                raise HTTPException(422, "File is empty")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Malformed multipart upload") from None
    provider, items, warnings = await run_in_threadpool(csv_bill if kind == "csv" else pdf_bill, data)
    bill_id = None
    if config.GCS_BUCKET:
        bill_id = await run_in_threadpool(save_bill, db, user.id, filename, kind, data, provider)
    response.headers["Cache-Control"] = "no-store"
    return ParsedBill(bill_id=bill_id, filename=filename, file_type=kind, provider=provider,
                      row_count=len(items), items=items, warnings=warnings)


def save_bill(db: Session, user_id: int, filename: str, kind: str, data: bytes, provider: str | None) -> int:
    provider_id = db.scalar(select(CloudProvider.id).where(CloudProvider.slug == provider)) if provider else None
    key = storage.object_key(user_id, kind)
    try:
        storage.upload_bill(key, data, kind)
    except storage.StorageError:
        raise HTTPException(503, "Bill could not be stored. Please try again.") from None
    try:
        bill = UploadedBill(user_id=user_id, provider_id=provider_id, original_filename=filename,
                            storage_key=key, file_type=kind, file_size=len(data), status="parsed")
        db.add(bill)
        db.flush()
        bill_id = bill.id
        db.commit()
        return bill_id
    except SQLAlchemyError:
        db.rollback()
        try:
            storage.delete_bill(key)
        except storage.StorageError:
            logging.getLogger(__name__).warning("Bill object cleanup failed")
        raise HTTPException(503, "Bill metadata could not be saved. Please try again.") from None


@router.delete("/{bill_id}", status_code=204)
def delete_bill(bill_id: int, user: Annotated[User, Depends(get_current_user)],
                db: Annotated[Session, Depends(get_db)]):
    bill = db.scalar(select(UploadedBill).where(
        UploadedBill.id == bill_id, UploadedBill.user_id == user.id
    ).with_for_update())
    if bill is None:
        raise HTTPException(404, "Bill not found")
    try:
        storage.delete_bill(bill.storage_key)
        db.delete(bill)
        db.commit()
    except (storage.StorageError, SQLAlchemyError):
        db.rollback()
        # Keep metadata on failure so a retry can finish deletion of a missing object.
        raise HTTPException(503, "Bill deletion could not be completed. Please retry.") from None
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
