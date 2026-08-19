"""SQLAlchemy models.

Schema notes that the README expands on:

* Columns the seed may violate (`shows.section`, `episodes.duration_seconds`) are
  **nullable**. The seed is deliberately imperfect and must load in full — business
  validity is enforced by the publish gate, not by the loader refusing rows. A schema
  that rejects the seed would hide exactly the problems we are asked to surface.
* `(content_group, language)` is a genuine partial unique index, because the challenge
  states it as a hard rule. The seed's one violator is quarantined at ingest rather
  than dropped, so the editor can see and resolve it.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class UserRole(str, enum.Enum):
    editor = "editor"
    admin = "admin"


class Status(str, enum.Enum):
    draft = "draft"
    published = "published"


class ArtworkKind(str, enum.Enum):
    poster = "poster"
    banner = "banner"
    thumbnail = "thumbnail"


class OwnerType(str, enum.Enum):
    show = "show"
    episode = "episode"


class PublishStatus(str, enum.Enum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    noop = "noop"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), nullable=False)


class Show(Base):
    __tablename__ = "shows"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    synopsis: Mapped[str | None] = mapped_column(Text)
    # Nullable on purpose: the seed contains a show with no section (DECISIONS D-005).
    section: Mapped[str | None] = mapped_column(String(100))
    categories: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    status: Mapped[Status] = mapped_column(
        Enum(Status, name="content_status"), default=Status.draft, nullable=False
    )
    featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    seasons: Mapped[list[Season]] = relationship(
        back_populates="show", cascade="all, delete-orphan", order_by="Season.number"
    )

    # Drives the publish snapshot query (WHERE status='published') and the CMS section
    # filter — the only two ways this table is scanned in bulk.
    __table_args__ = (Index("ix_shows_status_section", "status", "section"),)


class Season(Base):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    show_id: Mapped[int] = mapped_column(
        ForeignKey("shows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))

    show: Mapped[Show] = relationship(back_populates="seasons")
    episodes: Mapped[list[Episode]] = relationship(
        back_populates="season", cascade="all, delete-orphan", order_by="Episode.number"
    )

    __table_args__ = (
        UniqueConstraint("show_id", "number", name="uq_seasons_show_number"),
        CheckConstraint("number >= 0", name="ck_seasons_number_nonneg"),
    )


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(100), index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    synopsis: Mapped[str | None] = mapped_column(Text)
    # Nullable on purpose: publish-blocking, not insert-blocking.
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    content_group: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[Status] = mapped_column(
        Enum(Status, name="content_status", create_type=False),
        default=Status.draft,
        nullable=False,
    )
    # Set at ingest for rows that arrive already broken, so the validation report can
    # explain *why* a row was quarantined instead of only that it is invalid.
    seed_issue: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    season: Mapped[Season] = relationship(back_populates="episodes")

    __table_args__ = (
        # The challenge's hard rule. Partial, because content_group is optional:
        # NULL means "not a language variant", and many rows share that.
        Index(
            "uq_episodes_content_group_language",
            "content_group",
            "language",
            unique=True,
            postgresql_where=text("content_group IS NOT NULL"),
        ),
        Index("ix_episodes_season_number", "season_id", "number"),
    )


class Artwork(Base):
    __tablename__ = "artwork"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[OwnerType] = mapped_column(
        Enum(OwnerType, name="owner_type"), nullable=False
    )
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[ArtworkKind] = mapped_column(
        Enum(ArtworkKind, name="artwork_kind"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # One image per slot; re-uploading replaces rather than accumulates.
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "kind", name="uq_artwork_owner_kind"),
    )


class PublishRun(Base):
    __tablename__ = "publish_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[PublishStatus] = mapped_column(
        Enum(PublishStatus, name="publish_status"), nullable=False
    )
    counts: Mapped[dict | None] = mapped_column(JSON)
    catalog_key: Mapped[str | None] = mapped_column(String(500))
    checksum: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    actor: Mapped[User | None] = relationship()

    __table_args__ = (
        # At most one live catalogue. This partial unique index is also the pointer
        # lookup used by GET /catalog, so it earns its keep twice.
        Index(
            "uq_publish_runs_single_current",
            "is_current",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )
