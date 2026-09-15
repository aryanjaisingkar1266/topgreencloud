import csv
from datetime import date, datetime
from decimal import Decimal, localcontext
from io import StringIO
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import CarbonAnalysis, CarbonAnalysisItem, CloudProvider, UploadedBill, User

router = APIRouter(prefix="/carbon", tags=["carbon"])
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Quantity = Annotated[Decimal, Field(ge=0, max_digits=24, decimal_places=9, allow_inf_nan=False)]


class Usage(BaseModel):
    provider: Identifier | None = None
    service_name: Identifier | None = None
    service_category: Identifier | None = None
    region: Identifier | None = None
    usage_quantity: Quantity | None = None
    usage_unit: Identifier | None = None


class CalculationRequest(BaseModel):
    # Extra billing fields from Phase 5 are ignored, never used for estimation.
    provider: Identifier | None = None
    bill_id: int | None = Field(default=None, gt=0)
    items: list[Usage] = Field(min_length=1, max_length=10000)


class Source(BaseModel):
    name: Identifier
    url: HttpUrl
    methodology_version: Identifier
    source_date: date | None = None


class Coefficient(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: Literal["aws", "azure", "gcp"]
    service_name: Identifier
    service_category: Identifier
    region: Identifier
    usage_unit: Identifier
    kg_co2e_per_unit: Quantity
    source: Source


# Add only reviewed, traceable factors with exact applicability and units here.
# Source metadata alone does not establish scientific validity. No defaults apply.
# Review 2026-09-15: none of these methods publishes an exact factor for our key.
# AWS model 3.0 requires cluster/rack emissions and customer allocation:
# https://sustainability.aboutamazon.com/aws-customer-carbon-footprint-tool-methodology.pdf
# Google requires measured energy and hourly allocation:
# https://docs.cloud.google.com/carbon-footprint/docs/methodology
# Azure combines consumption with internal energy/carbon data:
# https://learn.microsoft.com/en-us/power-bi/connect-data/service-connect-to-emissions-impact-dashboard
# Cloud Carbon Footprint also needs utilization/conversion assumptions, not a direct factor:
# https://www.cloudcarbonfootprint.org/docs/methodology/
COEFFICIENTS: tuple[Coefficient, ...] = ()


class CalculatedItem(Usage):
    estimated_kg_co2e: Decimal
    kg_co2e_per_unit: Decimal
    source: Source


class UnsupportedItem(Usage):
    reason: str


class CalculationResult(BaseModel):
    analysis_id: int | None = None
    total_kg_co2e: Decimal | None
    methodology_version: str = "exact-unit-multiplication-v1"
    complete: bool
    calculated_items: list[CalculatedItem]
    unsupported_items: list[UnsupportedItem]
    warnings: list[str]


def calculate(data: CalculationRequest, coefficients: tuple[Coefficient, ...]) -> CalculationResult:
    calculated, unsupported = [], []
    # 24-digit operands, 48-digit products, and up to 10,000 additions fit exactly.
    with localcontext() as context:
        context.prec = 64
        total = Decimal(0)
        for item in data.items:
            provider = item.provider if "provider" in item.model_fields_set else data.provider
            values = item.model_dump() | {"provider": provider}
            reason = None
            if provider not in ("aws", "azure", "gcp"):
                reason = "Unknown or missing provider"
            elif item.usage_quantity is None:
                reason = "Missing usage quantity"
            elif any(values[field] is None for field in (
                "service_name", "service_category", "region", "usage_unit"
            )):
                reason = "Missing service, category, region, or usage unit"
            matches = [factor for factor in coefficients if all(
                getattr(factor, field) == values[field] for field in (
                    "provider", "service_name", "service_category", "region", "usage_unit"
                )
            )] if reason is None else []
            if reason is None and len(matches) != 1:
                reason = "No verified coefficient for this exact usage" if not matches else "Ambiguous coefficient applicability"
            if reason:
                unsupported.append(UnsupportedItem(**values, reason=reason))
                continue
            factor = matches[0]
            estimate = item.usage_quantity * factor.kg_co2e_per_unit
            total += estimate
            calculated.append(CalculatedItem(**values, estimated_kg_co2e=estimate,
                                             kg_co2e_per_unit=factor.kg_co2e_per_unit, source=factor.source))
    return CalculationResult(total_kg_co2e=total if calculated else None,
        complete=not unsupported, calculated_items=calculated, unsupported_items=unsupported,
        warnings=["Total covers calculated items only; unsupported emissions are unknown"] if unsupported else [])


class HistoryItem(BaseModel):
    analysis_id: int
    bill_id: int
    bill_filename: str
    provider_slug: str | None
    total_kg_co2e: Decimal | None
    methodology_version: str
    complete: bool
    created_at: datetime


def persist_analysis(db: Session, user_id: int, bill: UploadedBill,
                     provider_slug: str | None, result: CalculationResult) -> int:
    provider_id = bill.provider_id
    if provider_slug:
        provider_id = db.scalar(select(CloudProvider.id).where(CloudProvider.slug == provider_slug))
    analysis = CarbonAnalysis(user_id=user_id, bill_id=bill.id, provider_id=provider_id,
                              provider_slug=provider_slug, total_co2e=result.total_kg_co2e,
                              methodology_version=result.methodology_version, complete=result.complete)
    db.add(analysis)
    db.flush()
    for item in result.calculated_items:
        db.add(CarbonAnalysisItem(
            analysis_id=analysis.id, provider_slug=item.provider, service_name=item.service_name,
            service_category=item.service_category, region=item.region,
            usage_quantity=item.usage_quantity, usage_unit=item.usage_unit,
            estimated_co2e=item.estimated_kg_co2e, kg_co2e_per_unit=item.kg_co2e_per_unit,
            source_name=item.source.name, source_url=str(item.source.url),
            source_methodology_version=item.source.methodology_version,
            source_date=item.source.source_date,
        ))
    for item in result.unsupported_items:
        db.add(CarbonAnalysisItem(
            analysis_id=analysis.id, provider_slug=item.provider, service_name=item.service_name,
            service_category=item.service_category, region=item.region,
            usage_quantity=item.usage_quantity, usage_unit=item.usage_unit,
            unsupported_reason=item.reason,
        ))
    db.commit()
    return analysis.id


@router.post("/calculate", response_model=CalculationResult)
def calculate_carbon(data: CalculationRequest, response: Response,
                     user: Annotated[User, Depends(get_current_user)],
                     db: Annotated[Session, Depends(get_db)]):
    response.headers["Cache-Control"] = "no-store"
    result = calculate(data, COEFFICIENTS)
    if data.bill_id is None:
        return result
    bill = db.scalar(select(UploadedBill).where(
        UploadedBill.id == data.bill_id, UploadedBill.user_id == user.id
    ))
    if bill is None:
        raise HTTPException(404, "Bill not found")
    provider_slug = data.provider if data.provider in ("aws", "azure", "gcp") else None
    if provider_slug is None and bill.provider_id:
        provider_slug = db.scalar(select(CloudProvider.slug).where(CloudProvider.id == bill.provider_id))
    try:
        result.analysis_id = persist_analysis(db, user.id, bill, provider_slug, result)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Analysis could not be saved. Please retry.") from None
    return result


@router.get("/history", response_model=list[HistoryItem])
def history(response: Response, user: Annotated[User, Depends(get_current_user)],
            db: Annotated[Session, Depends(get_db)]):
    response.headers["Cache-Control"] = "no-store"
    rows = db.execute(
        select(CarbonAnalysis, UploadedBill.original_filename)
        .join(UploadedBill, CarbonAnalysis.bill_id == UploadedBill.id)
        .where(CarbonAnalysis.user_id == user.id)
        .order_by(CarbonAnalysis.created_at.desc(), CarbonAnalysis.id.desc())
        .limit(100)
    ).all()
    return [HistoryItem(
        analysis_id=analysis.id, bill_id=analysis.bill_id, bill_filename=filename,
        provider_slug=analysis.provider_slug, total_kg_co2e=analysis.total_co2e,
        methodology_version=analysis.methodology_version, complete=analysis.complete,
        created_at=analysis.created_at,
    ) for analysis, filename in rows]


def csv_cell(value, text: bool = False) -> str:
    if value is None:
        return ""
    result = value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    return "'" + result if text and result.startswith(("=", "+", "-", "@")) else result


@router.get("/history/{analysis_id}/export.csv")
def export_history(analysis_id: int, user: Annotated[User, Depends(get_current_user)],
                   db: Annotated[Session, Depends(get_db)]):
    row = db.execute(
        select(CarbonAnalysis, UploadedBill.original_filename)
        .join(UploadedBill, CarbonAnalysis.bill_id == UploadedBill.id)
        .where(CarbonAnalysis.id == analysis_id, CarbonAnalysis.user_id == user.id)
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Analysis not found")
    analysis, filename = row
    items = db.scalars(select(CarbonAnalysisItem).where(
        CarbonAnalysisItem.analysis_id == analysis.id
    ).order_by(CarbonAnalysisItem.id)).all()
    columns = [
        "analysis_id", "bill_id", "bill_filename", "analysis_created_at", "analysis_provider",
        "analysis_total_kg_co2e", "analysis_complete", "methodology_version", "item_provider",
        "service_name", "service_category", "region", "usage_quantity", "usage_unit",
        "estimated_kg_co2e", "kg_co2e_per_unit", "unsupported_reason", "source_name",
        "source_url", "source_methodology_version", "source_date",
    ]
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(columns)
    for item in items:
        writer.writerow([
            analysis.id, analysis.bill_id, csv_cell(filename, True), analysis.created_at.isoformat(),
            csv_cell(analysis.provider_slug, True), csv_cell(analysis.total_co2e), analysis.complete,
            csv_cell(analysis.methodology_version, True), csv_cell(item.provider_slug, True),
            csv_cell(item.service_name, True), csv_cell(item.service_category, True),
            csv_cell(item.region, True), csv_cell(item.usage_quantity), csv_cell(item.usage_unit, True),
            csv_cell(item.estimated_co2e), csv_cell(item.kg_co2e_per_unit),
            csv_cell(item.unsupported_reason, True), csv_cell(item.source_name, True),
            csv_cell(item.source_url, True), csv_cell(item.source_methodology_version, True),
            csv_cell(item.source_date),
        ])
    return Response(output.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="topgreencloud-analysis-{analysis.id}.csv"',
        "Cache-Control": "no-store",
    })
