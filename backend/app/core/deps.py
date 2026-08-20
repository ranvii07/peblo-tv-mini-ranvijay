"""Auth dependencies.

Roles are enforced here and nowhere else, so there is exactly one place to audit.
`require_admin` guards the one thing an editor may not do — publishing. Everything else
under `/api` takes `require_user`, including the validation report and run history: both
exist to tell editors what to fix and what happened, so hiding them from editors would
defeat their purpose. Catalogue, search, media and health take neither and are public —
that is what lets the viewer app work without any auth code at all.
"""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import forbidden, unauthorized
from app.core.security import decode_access_token
from app.models import User, UserRole


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized()
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise unauthorized("Your session has expired. Please sign in again.") from None
    except jwt.PyJWTError:
        raise unauthorized("That sign-in token isn't valid. Please sign in again.") from None

    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise unauthorized("That account no longer exists.")
    return user


def require_user(user: User = Depends(get_current_user)) -> User:
    """Any signed-in role. Editors and admins both have full CRUD."""
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Admin only. Publishing is the one thing an editor cannot do."""
    if user.role is not UserRole.admin:
        raise forbidden(
            "Publishing requires the admin role. Your account is an editor, so you can "
            "make and save changes, but an admin has to publish them."
        )
    return user


def get_optional_user(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> User | None:
    """For routes that behave differently when signed in but do not require it."""
    if not authorization:
        return None
    try:
        return get_current_user(authorization=authorization, db=db)
    except Exception:
        return None
