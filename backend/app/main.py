from contextlib import asynccontextmanager
import os
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.auth import database_error, router, validation_error
from app.config import validate_auth_config
from app.providers import router as providers_router
from app.bills import router as bills_router
from app.carbon import router as carbon_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_auth_config()
    yield

app = FastAPI(title="TopGreenCloud API", lifespan=lifespan)
frontend_origin = os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")
origin = urlsplit(frontend_origin)
if (origin.scheme not in ("http", "https") or not origin.netloc or
        origin.path or origin.query or origin.fragment or origin.username or
        origin.password or "*" in frontend_origin):
    raise RuntimeError("FRONTEND_URL must be a single HTTP(S) origin.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_exception_handler(RequestValidationError, validation_error)
app.add_exception_handler(SQLAlchemyError, database_error)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


app.include_router(router)
app.include_router(providers_router)
app.include_router(bills_router)
app.include_router(carbon_router)
