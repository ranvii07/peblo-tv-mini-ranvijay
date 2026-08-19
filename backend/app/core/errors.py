"""One error shape for the entire API.

Every failure — validation, auth, conflict, not-found — comes back as:

    {"error": {"code": "...", "message": "...", "details": {...}}}

`message` is always written for a human to read; the CMS renders it verbatim rather than
inventing its own copy. That is what keeps error text consistent between the API and the
UI, and it means a message only ever has to be written once, on the server.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str,
                 details: Any | None = None):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message
        self.details = details

    def body(self) -> dict:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            err["details"] = self.details
        return {"error": err}


def not_found(entity: str, entity_id: Any) -> ApiError:
    return ApiError(404, "not_found", f"No {entity} with id {entity_id} exists.")


def forbidden(message: str) -> ApiError:
    return ApiError(403, "forbidden", message)


def unauthorized(message: str = "Please sign in to continue.") -> ApiError:
    return ApiError(401, "unauthorized", message)


def conflict(code: str, message: str, details: Any | None = None) -> ApiError:
    return ApiError(409, code, message, details)


def unprocessable(code: str, message: str, details: Any | None = None) -> ApiError:
    return ApiError(422, code, message, details)
