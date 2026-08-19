"""Public read surface — the only endpoints the viewer app touches.

No authentication, no admin data, no database rows: `/api/catalog` serves the published
artifact and `/api/catalog/search` searches that same artifact. The viewer therefore
cannot see unpublished content even by accident, because unpublished content is not in
the file it reads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ApiError
from app.services import publish_service
from app.services.search import index_cache
from app.storage import Storage, get_storage

router = APIRouter(prefix="/api", tags=["public"])


def _load_or_404(db: Session, storage: Storage):
    loaded = publish_service.load_current_catalog(db, storage)
    if loaded is None:
        raise ApiError(
            404,
            "no_catalog_published",
            "Nothing has been published yet. Once the content team publishes, shows "
            "will appear here.",
        )
    return loaded


@router.get("/catalog")
def get_catalog(
    request: Request,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    if_none_match: str | None = Header(default=None),
) -> Response:
    catalog, checksum = _load_or_404(db, storage)
    etag = f'"{checksum}"'
    # The catalogue only changes when a publish succeeds, and its checksum is exactly
    # the identity of its content — so it is a perfect ETag.
    if if_none_match and if_none_match.strip() == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(
        catalog,
        headers={"ETag": etag, "Cache-Control": "public, max-age=60"},
    )


@router.get("/catalog/search")
def search_catalog(
    q: str | None = None,
    category: str | None = None,
    language: str | None = None,
    section: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> dict:
    catalog, checksum = _load_or_404(db, storage)
    index = index_cache.get(catalog, checksum)
    return index.search(q=q, category=category, language=language, section=section, limit=limit)
