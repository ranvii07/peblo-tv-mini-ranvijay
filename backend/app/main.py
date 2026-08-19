"""FastAPI application entrypoint."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import admin, artwork, auth, content, ops, public
from app.core.errors import ApiError

log = logging.getLogger("peblo")

app = FastAPI(
    title="Peblo TV Mini",
    version="1.0.0",
    description=(
        "CMS -> published catalogue -> viewer. Admin routes require the admin role; "
        "/api/catalog and /api/catalog/search are public and are all the viewer uses."
    ),
)

# In compose, nginx proxies /api and /media to this service so both UIs are same-origin
# and CORS never applies. This permissive setting exists only for running the Vite dev
# servers directly against the API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ApiError)
def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(exc.body(), status_code=exc.status_code)


@app.exception_handler(StarletteHTTPException)
def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Everything leaves through the same envelope, including FastAPI's own 404s."""
    return JSONResponse(
        {"error": {"code": "http_error", "message": str(exc.detail)}},
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "request"
    return JSONResponse(
        {
            "error": {
                "code": "invalid_request",
                "message": f"'{field}' isn't valid: {first.get('msg', 'check this field')}.",
                "details": {"fields": exc.errors()},
            }
        },
        status_code=422,
    )


@app.exception_handler(Exception)
def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Never leak a stack trace to a browser; always log it server-side."""
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        {
            "error": {
                "code": "internal_error",
                "message": "Something went wrong on our side. Please try again.",
            }
        },
        status_code=500,
    )


app.include_router(auth.router)
app.include_router(content.router)
app.include_router(artwork.router)
app.include_router(admin.router)
app.include_router(public.router)
app.include_router(ops.router)
