from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CloudProvider, SustainabilityMetric

router = APIRouter(prefix="/providers", tags=["providers"])
Database = Annotated[Session, Depends(get_db)]


class Provider(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    website_url: str | None
    description: str | None


class Metric(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    metric_name: str
    metric_value: str
    unit: str | None
    source_url: str
    source_date: date | None
    notes: str | None


class ProviderDetail(Provider):
    metrics: list[Metric]


def with_metrics(db: Session, providers: list[CloudProvider]) -> list[ProviderDetail]:
    # Fetch all metrics together; the existing mapping has no reverse relationship.
    grouped = {provider.id: [] for provider in providers}
    if grouped:
        metrics = db.scalars(select(SustainabilityMetric).where(
            SustainabilityMetric.provider_id.in_(grouped)
        ).order_by(SustainabilityMetric.metric_name, SustainabilityMetric.id))
        for metric in metrics:
            grouped[metric.provider_id].append(Metric.model_validate(metric))
    return [ProviderDetail(**Provider.model_validate(provider).model_dump(),
                           metrics=grouped[provider.id]) for provider in providers]


@router.get("", response_model=list[Provider])
def list_providers(
    db: Database,
    search: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    sort: Literal["name", "-name"] = "name",
):
    statement = select(CloudProvider)
    if search is not None:
        search = search.strip().lower()
        if not search:
            raise HTTPException(422, "Search must not be empty")
        statement = statement.where(or_(
            func.lower(CloudProvider.name).contains(search, autoescape=True),
            func.lower(CloudProvider.slug).contains(search, autoescape=True),
        ))
    ordering = func.lower(CloudProvider.name)
    return db.scalars(statement.order_by(
        ordering.desc() if sort == "-name" else ordering.asc(), CloudProvider.id
    )).all()


@router.get("/compare", response_model=list[ProviderDetail])
def compare_providers(
    db: Database,
    providers: Annotated[str, Query(min_length=1, max_length=483,
                                   description="Two to four comma-separated provider slugs, in display order")],
):
    slugs = [slug.strip() for slug in providers.split(",")]
    if not 2 <= len(slugs) <= 4 or any(not slug or len(slug) > 120 for slug in slugs):
        raise HTTPException(422, "Provide between 2 and 4 nonempty provider slugs")
    if len(set(slugs)) != len(slugs):
        raise HTTPException(422, "Provider slugs must be unique")
    found = {provider.slug: provider for provider in db.scalars(
        select(CloudProvider).where(CloudProvider.slug.in_(slugs))
    )}
    if len(found) != len(slugs):
        raise HTTPException(404, "One or more providers were not found")
    return with_metrics(db, [found[slug] for slug in slugs])


@router.get("/{slug}", response_model=ProviderDetail)
def provider_detail(db: Database, slug: Annotated[str, Path(min_length=1, max_length=120)]):
    provider = db.scalar(select(CloudProvider).where(CloudProvider.slug == slug))
    if provider is None:
        raise HTTPException(404, "Provider not found")
    return with_metrics(db, [provider])[0]
