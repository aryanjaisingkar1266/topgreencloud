from datetime import date
from decimal import Decimal, localcontext
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints

from app.auth import get_current_user
from app.models import User

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
COEFFICIENTS: tuple[Coefficient, ...] = ()


class CalculatedItem(Usage):
    estimated_kg_co2e: Decimal
    kg_co2e_per_unit: Decimal
    source: Source


class UnsupportedItem(Usage):
    reason: str


class CalculationResult(BaseModel):
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


@router.post("/calculate", response_model=CalculationResult)
def calculate_carbon(data: CalculationRequest, response: Response,
                     user: Annotated[User, Depends(get_current_user)]):
    response.headers["Cache-Control"] = "no-store"
    return calculate(data, COEFFICIENTS)
