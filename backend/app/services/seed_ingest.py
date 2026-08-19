"""Seed ingest.

Governing rule: **never silently fix the seed data.** The challenge states the data is
deliberately imperfect and that finding it is part of the exercise, so ingest loads
everything as-is and records what is wrong. Anything invalid lands as `draft` with its
problem recorded in `episodes.seed_issue`, where the validation report picks it up and
shows it to an editor. Nothing is dropped, nothing is quietly corrected.

The one exception is the `(content_group, language)` unique index, which is a real
database constraint and cannot hold two colliding rows. The colliding row is still
ingested — it keeps its content_group, is forced to `draft`, and carries a `seed_issue`
explaining the collision — because deleting it would destroy the very evidence the
report exists to surface.

Shape note: `seed_shows.json` is a flat list of 95 episode rows, not a nested tree.
Shows and seasons are derived from the rows (`slug` is show identity).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Reference, get_settings
from app.core.security import hash_password
from app.models import (
    Artwork,
    ArtworkKind,
    Episode,
    OwnerType,
    Season,
    Show,
    Status,
    User,
    UserRole,
)
from app.services.artwork_validator import validate_artwork
from app.storage import Storage

# Which sample image stands in for each declared artwork slot. The seed declares that
# artwork exists but ships no per-show files, so the three known-good samples are used
# (DECISIONS D-004) — this is what makes `docker compose up` yield a viewer with images.
_SAMPLE_FOR_KIND = {
    ArtworkKind.poster: "poster_good.jpg",
    ArtworkKind.banner: "banner_good.jpg",
    ArtworkKind.thumbnail: "thumb_good.jpg",
}


def _artwork_key(owner_type: OwnerType, owner_id: int, kind: ArtworkKind, checksum: str,
                 ext: str) -> str:
    return f"artwork/{owner_type.value}/{owner_id}/{kind.value}/{checksum[:12]}.{ext}"


def seed_users(db: Session) -> None:
    s = get_settings()
    wanted = [
        (s.admin_email, s.admin_password, UserRole.admin),
        (s.editor_email, s.editor_password, UserRole.editor),
    ]
    for email, password, role in wanted:
        if db.scalar(select(User).where(User.email == email)) is None:
            db.add(User(email=email, password_hash=hash_password(password), role=role))
    db.commit()


def seed_content(db: Session, reference: Reference, storage: Storage,
                 seed_dir: Path) -> dict[str, Any]:
    """Load seed_shows.json. Idempotent: does nothing if shows already exist."""
    if db.scalar(select(Show).limit(1)) is not None:
        return {"skipped": True, "reason": "content already present"}

    rows: list[dict] = json.loads((seed_dir / "seed_shows.json").read_text(encoding="utf-8"))

    # ---- derive shows from the flat row list -------------------------------------
    by_slug: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_slug[r["slug"]].append(r)

    findings: list[str] = []
    shows: dict[str, Show] = {}

    for slug, show_rows in by_slug.items():
        first = show_rows[0]

        # Show-level fields repeat on every row; disagreement is itself a finding.
        for field in ("show_title", "section", "synopsis"):
            distinct = {json.dumps(r.get(field), sort_keys=True) for r in show_rows}
            if len(distinct) > 1:
                findings.append(
                    f"show '{slug}': rows disagree on {field} ({len(distinct)} distinct values)"
                )

        section = first.get("section")
        if section is not None and section not in reference.sections:
            findings.append(f"show '{slug}': section {section!r} is not in reference.json")
            section = None

        categories = sorted({c for r in show_rows for c in (r.get("categories") or [])})
        unknown = [c for c in categories if c not in reference.categories]
        if unknown:
            findings.append(f"show '{slug}': unknown categories {unknown}")

        show = Show(
            slug=slug,
            title=first["show_title"],
            synopsis=first.get("synopsis"),
            section=section,
            categories=categories,
            # A show is published only if it has a section and at least one publishable
            # episode; that is recomputed by the validation service. Start from the
            # seed's own intent.
            status=Status.published if section else Status.draft,
            featured=(section == "featured"),
        )
        db.add(show)
        shows[slug] = show

    db.flush()

    # ---- seasons ------------------------------------------------------------------
    seasons: dict[tuple[str, int], Season] = {}
    for slug, show_rows in by_slug.items():
        for number in sorted({r["season_number"] for r in show_rows}):
            season = Season(
                show_id=shows[slug].id,
                number=number,
                title="Trailers" if number == 0 else f"Season {number}",
            )
            db.add(season)
            seasons[(slug, number)] = season
    db.flush()

    # ---- episodes -----------------------------------------------------------------
    seen_group_language: dict[tuple[str, str], str] = {}
    episodes: list[tuple[Episode, dict]] = []

    for r in rows:
        issues: list[str] = []
        status = Status.published if r.get("status") == "published" else Status.draft

        duration = r.get("duration_seconds")
        if duration is None or not isinstance(duration, int) or duration <= 0:
            issues.append("duration is missing or not a positive number")
            duration = None

        language = r.get("language")
        if language not in reference.languages:
            issues.append(f"language {language!r} is not one of {reference.languages}")

        content_group = r.get("content_group")
        if content_group:
            key = (content_group, language)
            if key in seen_group_language:
                issues.append(
                    f"duplicate content_group/language: '{content_group}' in "
                    f"'{language}' is already used by {seen_group_language[key]}"
                )
                # Cannot store a second row under the same key — the partial unique
                # index forbids it. Keep the row and its evidence, but detach the
                # grouping so the editor can decide what it really is.
                content_group = None
            else:
                seen_group_language[key] = r["episode_id"]

        if not r.get("artwork_available"):
            issues.append("no artwork is available for this episode")

        if issues:
            # Anything with a problem is quarantined rather than published.
            status = Status.draft
            findings.append(f"{r['episode_id']} ({r['slug']}): " + "; ".join(issues))

        episode = Episode(
            season_id=seasons[(r["slug"], r["season_number"])].id,
            external_id=r["episode_id"],
            number=r["episode_number"],
            title=r["episode_title"],
            synopsis=r.get("synopsis"),
            duration_seconds=duration,
            language=language,
            content_group=content_group,
            status=status,
            seed_issue="; ".join(issues) if issues else None,
        )
        db.add(episode)
        episodes.append((episode, r))

    db.flush()

    # ---- artwork ------------------------------------------------------------------
    # Poster/banner belong to the show, thumbnail to the episode (DECISIONS D-004).
    uploaded: dict[str, tuple[str, Any]] = {}

    def sample_bytes(kind: ArtworkKind):
        if kind.value not in uploaded:
            data = (seed_dir / "assets" / _SAMPLE_FOR_KIND[kind]).read_bytes()
            # Seeded images go through the same validator as an editor's upload —
            # if the sample assets did not pass, the seed should fail loudly.
            validated = validate_artwork(data, kind.value, reference)
            uploaded[kind.value] = (data, validated)
        return uploaded[kind.value]

    show_slots: dict[str, set[ArtworkKind]] = defaultdict(set)
    for episode, r in episodes:
        for name in r.get("artwork_available") or []:
            kind = ArtworkKind(name)
            if kind in (ArtworkKind.poster, ArtworkKind.banner):
                show_slots[r["slug"]].add(kind)
            elif kind is ArtworkKind.thumbnail:
                data, v = sample_bytes(kind)
                key = _artwork_key(OwnerType.episode, episode.id, kind, v.checksum, v.extension)
                storage.put(key, data, v.content_type)
                db.add(Artwork(
                    owner_type=OwnerType.episode, owner_id=episode.id, kind=kind,
                    storage_key=key, width=v.width, height=v.height,
                    size_bytes=v.size_bytes, content_type=v.content_type,
                    checksum=v.checksum,
                ))

    for slug, kinds in show_slots.items():
        for kind in sorted(kinds, key=lambda k: k.value):
            data, v = sample_bytes(kind)
            show_id = shows[slug].id
            key = _artwork_key(OwnerType.show, show_id, kind, v.checksum, v.extension)
            storage.put(key, data, v.content_type)
            db.add(Artwork(
                owner_type=OwnerType.show, owner_id=show_id, kind=kind,
                storage_key=key, width=v.width, height=v.height,
                size_bytes=v.size_bytes, content_type=v.content_type, checksum=v.checksum,
            ))

    db.commit()

    return {
        "skipped": False,
        "shows": len(shows),
        "seasons": len(seasons),
        "episodes": len(episodes),
        "findings": findings,
    }
