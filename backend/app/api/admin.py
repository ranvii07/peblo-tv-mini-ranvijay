"""Admin-only surface: validation report, publish, run history.

Every route here takes `require_admin` except the validation report, which an editor
must be able to read — the report exists to tell editors what to fix, so hiding it from
them would defeat its purpose. Editors can read it; only admins can act on it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Reference, get_reference
from app.core.db import get_db
from app.core.deps import require_admin, require_user
from app.core.errors import ApiError, conflict
from app.models import PublishRun, User
from app.services import publish_service
from app.services.validation import collect_issues
from app.storage import Storage, get_storage

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/validation-report")
def validation_report(
    db: Session = Depends(get_db),
    reference: Reference = Depends(get_reference),
    _: User = Depends(require_user),
) -> dict:
    return collect_issues(db, reference)


@router.post("/catalog/publish")
def publish_catalog(
    db: Session = Depends(get_db),
    reference: Reference = Depends(get_reference),
    storage: Storage = Depends(get_storage),
    user: User = Depends(require_admin),
) -> dict:
    report = collect_issues(db, reference)
    if report["blocking_publish"]:
        raise ApiError(
            422,
            "publish_blocked",
            f"{report['counts']['blockers']} problems have to be fixed before this can "
            "be published. They're listed on the publish page.",
            {"report": report},
        )
    try:
        return publish_service.publish(db, reference, storage, actor_id=user.id)
    except publish_service.PublishInProgress:
        raise conflict(
            "publish_in_progress",
            "Someone else is publishing right now. Give it a moment and try again.",
        ) from None


@router.get("/publish-runs")
def publish_runs(
    db: Session = Depends(get_db),
    _: User = Depends(require_user),
    limit: int = 25,
) -> dict:
    runs = db.scalars(select(PublishRun).order_by(PublishRun.started_at.desc()).limit(limit)).all()
    return {
        "items": [
            {
                "id": r.id,
                "status": r.status.value,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "actor": r.actor.email if r.actor else None,
                "counts": r.counts,
                "checksum": (r.checksum or "")[:12],
                "error": r.error,
                "is_current": r.is_current,
            }
            for r in runs
        ]
    }
