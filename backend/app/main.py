from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.auth import database_error, router, validation_error
from app.config import validate_auth_config
from app.providers import router as providers_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_auth_config()
    yield

app = FastAPI(title="TopGreenCloud API", lifespan=lifespan)
app.add_exception_handler(RequestValidationError, validation_error)
app.add_exception_handler(SQLAlchemyError, database_error)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


app.include_router(router)
app.include_router(providers_router)
