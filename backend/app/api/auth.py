from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.errors import ApiError
from app.core.security import create_access_token, verify_password
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.scalar(select(User).where(User.email == payload.email))
    # Same message and same code whether the address is unknown or the password is
    # wrong — telling an attacker which half they got right is free information.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise ApiError(401, "invalid_credentials", "That email and password don't match.")
    token = create_access_token(user_id=user.id, email=user.email, role=user.role.value)
    return LoginResponse(
        access_token=token,
        user={"id": user.id, "email": user.email, "role": user.role.value},
    )


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {"id": user.id, "email": user.email, "role": user.role.value}
