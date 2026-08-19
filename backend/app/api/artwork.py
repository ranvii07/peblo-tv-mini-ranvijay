"""Artwork upload.

All validation is server-side (`app.services.artwork_validator`). The CMS runs some of
the same checks in the browser purely so an editor gets feedback before a round trip —
but the upload always happens and the server's verdict is always what is displayed.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Reference, get_reference
from app.core.db import get_db
from app.core.deps import require_user
from app.core.errors import ApiError, not_found
from app.models import Artwork, ArtworkKind, Episode, OwnerType, Show, User
from app.services.artwork_validator import ArtworkValidationError, validate_artwork
from app.storage import Storage, get_storage

router = APIRouter(prefix="/api", tags=["artwork"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # hard stop before anything reaches Pillow


@router.post("/artwork", status_code=201)
async def upload_artwork(
    file: UploadFile = File(...),
    owner_type: Literal["show", "episode"] = Form(...),
    owner_id: int = Form(...),
    kind: Literal["poster", "banner", "thumbnail"] = Form(...),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    reference: Reference = Depends(get_reference),
    _: User = Depends(require_user),
) -> dict:
    owner = OwnerType(owner_type)
    if owner is OwnerType.show:
        if db.get(Show, owner_id) is None:
            raise not_found("show", owner_id)
    else:
        if db.get(Episode, owner_id) is None:
            raise not_found("episode", owner_id)

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(
            413,
            "file_too_large",
            "That file is far larger than any artwork should be. Please upload an "
            "image under 10 MB.",
        )

    try:
        validated = validate_artwork(data, kind, reference)
    except ArtworkValidationError as e:
        # Surface the validator's editor-readable message unchanged.
        raise ApiError(
            422, e.code, e.message, {"expected": e.expected, "received": e.received}
        ) from None

    art_kind = ArtworkKind(kind)
    key = (
        f"artwork/{owner.value}/{owner_id}/{art_kind.value}/"
        f"{validated.checksum[:12]}.{validated.extension}"
    )
    storage.put(key, data, validated.content_type)

    existing = db.scalar(
        select(Artwork).where(
            Artwork.owner_type == owner,
            Artwork.owner_id == owner_id,
            Artwork.kind == art_kind,
        )
    )
    if existing is not None:
        old_key = existing.storage_key
        existing.storage_key = key
        existing.width = validated.width
        existing.height = validated.height
        existing.size_bytes = validated.size_bytes
        existing.content_type = validated.content_type
        existing.checksum = validated.checksum
        record = existing
        db.commit()
        # Only after the row commits — otherwise a failed commit would have deleted
        # the image the database still points at.
        if old_key != key:
            try:
                storage.delete(old_key)
            except Exception:
                pass
    else:
        record = Artwork(
            owner_type=owner,
            owner_id=owner_id,
            kind=art_kind,
            storage_key=key,
            width=validated.width,
            height=validated.height,
            size_bytes=validated.size_bytes,
            content_type=validated.content_type,
            checksum=validated.checksum,
        )
        db.add(record)
        db.commit()

    db.refresh(record)
    return {
        "id": record.id,
        "owner_type": record.owner_type.value,
        "owner_id": record.owner_id,
        "kind": record.kind.value,
        "width": record.width,
        "height": record.height,
        "size_bytes": record.size_bytes,
        "url": storage.public_url(record.storage_key),
    }


@router.delete("/artwork/{artwork_id}", status_code=204)
def delete_artwork(
    artwork_id: int,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    _: User = Depends(require_user),
):
    record = db.get(Artwork, artwork_id)
    if record is None:
        raise not_found("artwork", artwork_id)
    key = record.storage_key
    db.delete(record)
    db.commit()
    try:
        storage.delete(key)
    except Exception:
        pass
