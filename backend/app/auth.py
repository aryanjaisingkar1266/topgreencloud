from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])
hasher = PasswordHasher()
dummy_hash = hasher.hash("unused timing equalization password")
bearer = HTTPBearer(auto_error=False)
Database = Annotated[Session, Depends(get_db)]


async def validation_error(request: Request, exc: RequestValidationError):
    # FastAPI's default validation response can echo submitted passwords.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(error["loc"]), "type": error["type"], "msg": "Invalid input"}
        for error in exc.errors()
    ]})


async def database_error(request: Request, exc: SQLAlchemyError):
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"})


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    email: EmailStr
    password: str = Field(min_length=1, max_length=128, repr=False)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value):
        return value.lower()


class Registration(Credentials):
    password: str = Field(min_length=12, max_length=128, repr=False)


class PublicUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    is_active: bool
    is_admin: bool
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


def verify_password(password: str, encoded: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def create_token(user_id: int) -> str:
    config.validate_auth_config()
    return jwt.encode(
        {"sub": str(user_id), "exp": datetime.now(timezone.utc) + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)},
        config.JWT_SECRET, algorithm=config.JWT_ALGORITHM,
    )


def get_current_user(db: Database, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> User:
    unauthorized = HTTPException(401, "Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"})
    if credentials is None or len(credentials.credentials) > 4096:
        raise unauthorized
    config.validate_auth_config()
    try:
        claims = jwt.decode(credentials.credentials, config.JWT_SECRET,
                            algorithms=[config.JWT_ALGORITHM], options={"require": ["sub", "exp"]})
        subject = claims["sub"]
        if type(claims["exp"]) is not int:
            raise ValueError
        if not isinstance(subject, str) or not subject.isascii() or not subject.isdecimal() or len(subject) > 10:
            raise ValueError
        user_id = int(subject)
        if not 0 < user_id <= 2147483647:
            raise ValueError
    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError):
        raise unauthorized from None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


@router.post("/register", response_model=PublicUser, status_code=201)
def register(data: Registration, db: Database, response: Response):
    if db.scalar(select(User).where(func.lower(User.email) == data.email)):
        raise HTTPException(409, "Email is already registered")
    user = User(email=data.email, password_hash=hasher.hash(data.password), is_admin=False, is_active=True)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # The unique index also handles concurrent registrations.
        if db.scalar(select(User.id).where(func.lower(User.email) == data.email)):
            raise HTTPException(409, "Email is already registered") from None
        raise HTTPException(503, "Registration is temporarily unavailable") from None
    db.refresh(user)
    response.headers["Cache-Control"] = "no-store"
    return user


@router.post("/login", response_model=Token)
def login(data: Credentials, db: Database, response: Response):
    user = db.scalar(select(User).where(func.lower(User.email) == data.email))
    valid = verify_password(data.password, user.password_hash if user else dummy_hash)
    if not valid or user is None or not user.is_active:
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    response.headers["Cache-Control"] = "no-store"
    return Token(access_token=create_token(user.id))


@router.get("/me", response_model=PublicUser)
def me(user: Annotated[User, Depends(get_current_user)], response: Response):
    response.headers["Cache-Control"] = "no-store"
    return user
