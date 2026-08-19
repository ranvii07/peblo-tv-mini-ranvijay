"""CRUD for shows, seasons and episodes.

Filters on the list endpoint compose with AND and are applied in SQL with server-side
pagination — the CMS never receives rows it will not display.

Status transitions to `published` re-run the same validation the publish job uses, so a
row can never be marked published while it would break the catalogue.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import Reference, get_reference
from app.core.db import get_db
from app.core.deps import require_user
from app.core.errors import conflict, not_found, unprocessable
from app.models import (
    Artwork,
    Episode,
    OwnerType,
    Season,
    Show,
    Status,
    User,
)
from app.services.validation import episode_blockers, show_blockers, _artwork_index
from app.storage import Storage, get_storage

router = APIRouter(prefix="/api", tags=["content"])


# --------------------------------------------------------------------------- schemas
class ShowIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    slug: str | None = Field(default=None, max_length=255)
    synopsis: str | None = None
    section: str | None = None
    categories: list[str] = Field(default_factory=list)
    featured: bool = False


class ShowPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    synopsis: str | None = None
    section: str | None = None
    categories: list[str] | None = None
    featured: bool | None = None
    status: Literal["draft", "published"] | None = None


class SeasonIn(BaseModel):
    number: int = Field(ge=0)
    title: str | None = None


class EpisodeIn(BaseModel):
    number: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=500)
    synopsis: str | None = None
    duration_seconds: int | None = Field(default=None, ge=1)
    language: str
    content_group: str | None = None


class EpisodePatch(BaseModel):
    number: int | None = Field(default=None, ge=0)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    synopsis: str | None = None
    duration_seconds: int | None = Field(default=None, ge=1)
    language: str | None = None
    content_group: str | None = None
    status: Literal["draft", "published"] | None = None


# --------------------------------------------------------------------------- helpers
def _artwork_for(db: Session, owner_type: OwnerType, owner_id: int,
                 storage: Storage) -> dict:
    rows = db.scalars(
        select(Artwork).where(
            Artwork.owner_type == owner_type, Artwork.owner_id == owner_id
        )
    ).all()
    return {
        a.kind.value: {
            "url": storage.public_url(a.storage_key),
            "width": a.width,
            "height": a.height,
            "size_bytes": a.size_bytes,
        }
        for a in rows
    }


def _episode_dict(ep: Episode, db: Session, storage: Storage) -> dict:
    return {
        "id": ep.id,
        "season_id": ep.season_id,
        "external_id": ep.external_id,
        "number": ep.number,
        "title": ep.title,
        "synopsis": ep.synopsis,
        "duration_seconds": ep.duration_seconds,
        "language": ep.language,
        "content_group": ep.content_group,
        "status": ep.status.value,
        "seed_issue": ep.seed_issue,
        "artwork": _artwork_for(db, OwnerType.episode, ep.id, storage),
    }


def _show_detail(show: Show, db: Session, storage: Storage) -> dict:
    return {
        "id": show.id,
        "slug": show.slug,
        "title": show.title,
        "synopsis": show.synopsis,
        "section": show.section,
        "categories": list(show.categories or []),
        "status": show.status.value,
        "featured": show.featured,
        "artwork": _artwork_for(db, OwnerType.show, show.id, storage),
        "seasons": [
            {
                "id": s.id,
                "number": s.number,
                "title": s.title,
                "episodes": [_episode_dict(e, db, storage) for e in s.episodes],
            }
            for s in show.seasons
        ],
    }


def _validate_against_reference(section: str | None, categories: list[str] | None,
                                reference: Reference) -> None:
    if section is not None and section not in reference.sections:
        raise unprocessable(
            "unknown_section",
            f"'{section}' isn't one of the available sections "
            f"({', '.join(reference.sections)}).",
        )
    for c in categories or []:
        if c not in reference.categories:
            raise unprocessable(
                "unknown_category",
                f"'{c}' isn't an approved category. Approved categories are: "
                f"{', '.join(reference.categories)}.",
            )


def _slugify(title: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-") or "show"


# ----------------------------------------------------------------------------- shows
@router.get("/shows")
def list_shows(
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    _: User = Depends(require_user),
    q: str | None = None,
    section: str | None = None,
    status: Literal["draft", "published"] | None = None,
    language: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    stmt = select(Show)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Show.title.ilike(like), Show.synopsis.ilike(like)))
    if section:
        stmt = stmt.where(Show.section == section)
    if status:
        stmt = stmt.where(Show.status == Status(status))
    if language:
        # "shows that have at least one episode in this language"
        stmt = stmt.where(
            Show.id.in_(
                select(Season.show_id)
                .join(Episode, Episode.season_id == Season.id)
                .where(Episode.language == language)
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Show.title)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .options(selectinload(Show.seasons).selectinload(Season.episodes))
    ).all()

    items = []
    for show in rows:
        episodes = [e for s in show.seasons for e in s.episodes]
        items.append({
            "id": show.id,
            "slug": show.slug,
            "title": show.title,
            "section": show.section,
            "status": show.status.value,
            "featured": show.featured,
            "categories": list(show.categories or []),
            "episode_count": len(episodes),
            "languages": sorted({e.language for e in episodes}),
            "updated_at": show.updated_at.isoformat() if show.updated_at else None,
            "artwork": _artwork_for(db, OwnerType.show, show.id, storage),
        })

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


@router.post("/shows", status_code=201)
def create_show(
    payload: ShowIn,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    reference: Reference = Depends(get_reference),
    _: User = Depends(require_user),
) -> dict:
    _validate_against_reference(payload.section, payload.categories, reference)
    slug = payload.slug or _slugify(payload.title)
    if db.scalar(select(Show).where(Show.slug == slug)):
        raise conflict("duplicate_slug", f"A show with the address '{slug}' already exists.")
    show = Show(
        slug=slug,
        title=payload.title,
        synopsis=payload.synopsis,
        section=payload.section,
        categories=payload.categories,
        featured=payload.featured,
        status=Status.draft,
    )
    db.add(show)
    db.commit()
    db.refresh(show)
    return _show_detail(show, db, storage)


@router.get("/shows/{show_id}")
def get_show(
    show_id: int,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    _: User = Depends(require_user),
) -> dict:
    show = db.scalar(
        select(Show).where(Show.id == show_id)
        .options(selectinload(Show.seasons).selectinload(Season.episodes))
    )
    if show is None:
        raise not_found("show", show_id)
    return _show_detail(show, db, storage)


@router.patch("/shows/{show_id}")
def update_show(
    show_id: int,
    payload: ShowPatch,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    reference: Reference = Depends(get_reference),
    _: User = Depends(require_user),
) -> dict:
    show = db.scalar(
        select(Show).where(Show.id == show_id)
        .options(selectinload(Show.seasons).selectinload(Season.episodes))
    )
    if show is None:
        raise not_found("show", show_id)

    data = payload.model_dump(exclude_unset=True)
    _validate_against_reference(data.get("section"), data.get("categories"), reference)

    new_status = data.pop("status", None)
    for k, v in data.items():
        setattr(show, k, v)

    if new_status == "published":
        # Same rules the publish job applies — a show cannot be marked published if
        # publishing it would fail.
        db.flush()
        artwork = _artwork_index(db)
        publishable = sum(
            1
            for s in show.seasons
            for e in s.episodes
            if e.status is Status.published
            and not any(
                i.severity == "blocker"
                for i in episode_blockers(e, show, s, reference, artwork)
            )
        )
        blockers = [
            i for i in show_blockers(show, reference, artwork, publishable)
            if i.severity == "blocker"
        ]
        if blockers:
            db.rollback()
            raise unprocessable(
                "not_publishable",
                "This show isn't ready to publish yet.",
                {"issues": [i.to_dict() for i in blockers]},
            )
        show.status = Status.published
    elif new_status == "draft":
        show.status = Status.draft

    db.commit()
    db.refresh(show)
    return _show_detail(show, db, storage)


@router.delete("/shows/{show_id}", status_code=204)
def delete_show(
    show_id: int,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    _: User = Depends(require_user),
):
    show = db.get(Show, show_id)
    if show is None:
        raise not_found("show", show_id)
    # Storage objects are removed best-effort; a leftover image is harmless, whereas
    # failing the delete because a file was already gone would not be.
    episode_ids = [e.id for s in show.seasons for e in s.episodes]
    arts = db.scalars(
        select(Artwork).where(
            or_(
                (Artwork.owner_type == OwnerType.show) & (Artwork.owner_id == show_id),
                (Artwork.owner_type == OwnerType.episode)
                & (Artwork.owner_id.in_(episode_ids or [-1])),
            )
        )
    ).all()
    for a in arts:
        try:
            storage.delete(a.storage_key)
        except Exception:
            pass
        db.delete(a)
    db.delete(show)
    db.commit()


# --------------------------------------------------------------------------- seasons
@router.post("/shows/{show_id}/seasons", status_code=201)
def create_season(
    show_id: int,
    payload: SeasonIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_user),
) -> dict:
    if db.get(Show, show_id) is None:
        raise not_found("show", show_id)
    season = Season(show_id=show_id, number=payload.number, title=payload.title)
    db.add(season)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise conflict(
            "duplicate_season",
            f"This show already has a season {payload.number}.",
        ) from None
    db.refresh(season)
    return {"id": season.id, "show_id": season.show_id, "number": season.number,
            "title": season.title, "episodes": []}


@router.delete("/seasons/{season_id}", status_code=204)
def delete_season(
    season_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_user),
):
    season = db.get(Season, season_id)
    if season is None:
        raise not_found("season", season_id)
    db.delete(season)
    db.commit()


# -------------------------------------------------------------------------- episodes
@router.post("/seasons/{season_id}/episodes", status_code=201)
def create_episode(
    season_id: int,
    payload: EpisodeIn,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    reference: Reference = Depends(get_reference),
    _: User = Depends(require_user),
) -> dict:
    if db.get(Season, season_id) is None:
        raise not_found("season", season_id)
    if payload.language not in reference.languages:
        raise unprocessable(
            "unknown_language",
            f"'{payload.language}' isn't a supported language "
            f"({', '.join(reference.languages)}).",
        )
    episode = Episode(
        season_id=season_id,
        number=payload.number,
        title=payload.title,
        synopsis=payload.synopsis,
        duration_seconds=payload.duration_seconds,
        language=payload.language,
        content_group=payload.content_group,
        status=Status.draft,
    )
    db.add(episode)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise conflict(
            "duplicate_content_group",
            f"Another episode is already the {payload.language} version of content "
            f"group '{payload.content_group}'. Each language can appear only once per "
            "group.",
        ) from None
    db.refresh(episode)
    return _episode_dict(episode, db, storage)


@router.patch("/episodes/{episode_id}")
def update_episode(
    episode_id: int,
    payload: EpisodePatch,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    reference: Reference = Depends(get_reference),
    _: User = Depends(require_user),
) -> dict:
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise not_found("episode", episode_id)

    data = payload.model_dump(exclude_unset=True)
    if "language" in data and data["language"] not in reference.languages:
        raise unprocessable(
            "unknown_language",
            f"'{data['language']}' isn't a supported language "
            f"({', '.join(reference.languages)}).",
        )

    new_status = data.pop("status", None)
    for k, v in data.items():
        setattr(episode, k, v)

    if new_status == "published":
        db.flush()
        season = db.get(Season, episode.season_id)
        show = db.get(Show, season.show_id)
        artwork = _artwork_index(db)
        blockers = [
            i for i in episode_blockers(episode, show, season, reference, artwork)
            if i.severity == "blocker"
        ]
        if blockers:
            db.rollback()
            raise unprocessable(
                "not_publishable",
                "This episode isn't ready to publish yet.",
                {"issues": [i.to_dict() for i in blockers]},
            )
        episode.status = Status.published
        # Clearing the import flag records that a human has resolved it.
        episode.seed_issue = None
    elif new_status == "draft":
        episode.status = Status.draft

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise conflict(
            "duplicate_content_group",
            f"Another episode is already the {episode.language} version of content "
            f"group '{episode.content_group}'. Each language can appear only once "
            "per group.",
        ) from None
    db.refresh(episode)
    return _episode_dict(episode, db, storage)


@router.delete("/episodes/{episode_id}", status_code=204)
def delete_episode(
    episode_id: int,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    _: User = Depends(require_user),
):
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise not_found("episode", episode_id)
    for a in db.scalars(
        select(Artwork).where(
            Artwork.owner_type == OwnerType.episode, Artwork.owner_id == episode_id
        )
    ).all():
        try:
            storage.delete(a.storage_key)
        except Exception:
            pass
        db.delete(a)
    db.delete(episode)
    db.commit()
