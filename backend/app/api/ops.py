"""Health and media serving.

The health check verifies *dependencies*, not just that the process is answering. A
process that is up but cannot reach Postgres or write to storage is not healthy, and
reporting it as healthy is how outages stay invisible until a user finds them.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ApiError
from app.services import publish_service
from app.storage import Storage, get_storage

router = APIRouter(tags=["ops"])


@router.get("/api/health")
def health(
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> Response:
    checks: dict[str, object] = {}
    ok = True

    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        ok = False
        checks["db"] = f"error: {type(e).__name__}"

    # Actually exercise a write and a delete — "the disk is mounted read-only" is a
    # real failure mode that a existence check would miss.
    probe_key = "health/probe.txt"
    try:
        storage.put(probe_key, b"ok", "text/plain")
        storage.get(probe_key)
        storage.delete(probe_key)
        checks["storage"] = "ok"
    except Exception as e:
        ok = False
        checks["storage"] = f"error: {type(e).__name__}"

    try:
        run = publish_service.current_run(db)
        if run is None:
            checks["current_catalog"] = None
        else:
            age = (datetime.now(timezone.utc) - run.started_at).total_seconds()
            checks["current_catalog"] = {
                "run_id": run.id,
                "age_seconds": int(age),
                "checksum": (run.checksum or "")[:12],
            }
    except Exception as e:
        ok = False
        checks["current_catalog"] = f"error: {type(e).__name__}"

    return JSONResponse(
        {"status": "ok" if ok else "degraded", **checks},
        status_code=200 if ok else 503,
    )


@router.get("/media/{key:path}")
def media(key: str, storage: Storage = Depends(get_storage)) -> Response:
    """Serve stored objects.

    With STORAGE_BACKEND=r2 this route is unnecessary — artwork would be served from the
    bucket's public domain and this would become a redirect, or simply go unused.
    """
    try:
        data = storage.get(key)
    except (FileNotFoundError, ValueError):
        raise ApiError(404, "not_found", "That image no longer exists.") from None

    ext = key.rsplit(".", 1)[-1].lower()
    content_type = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "json": "application/json",
    }.get(ext, "application/octet-stream")
    return Response(
        data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
