"""Publish-readiness rules — one implementation, two callers.

`GET /api/admin/validation-report` and the publish gate must never disagree: a report
saying "ready" followed by a publish that refuses would be worse than either alone. So
both call `collect_issues` and differ only in presentation.

Issues are grouped **by show**, because that is the unit an editor actually works on.
Every issue names the entity it concerns, so the CMS can deep-link straight to the form
that fixes it, and every message says what to do rather than merely what is wrong.

Severity:
  * `blocker` — publish cannot proceed while this exists.
  * `warning` — surfaced for a human to judge, does not block. Used for things the data
    hints at but does not prove, where guessing would be worse than asking.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Reference
from app.models import Artwork, ArtworkKind, Episode, OwnerType, Season, Show, Status


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    entity_type: str
    entity_id: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entity"] = {"type": d.pop("entity_type"), "id": d.pop("entity_id")}
        return d


def _artwork_index(db: Session) -> dict[tuple[str, int], set[str]]:
    """(owner_type, owner_id) -> {kinds present}. One query, not N."""
    index: dict[tuple[str, int], set[str]] = {}
    for owner_type, owner_id, kind in db.execute(
        select(Artwork.owner_type, Artwork.owner_id, Artwork.kind)
    ):
        index.setdefault((owner_type.value, owner_id), set()).add(kind.value)
    return index


def effective_thumbnail(
    episode_id: int, show_id: int, artwork: dict[tuple[str, int], set[str]]
) -> bool:
    """An episode's thumbnail may come from the episode or fall back to its show."""
    return ArtworkKind.thumbnail.value in artwork.get(
        (OwnerType.episode.value, episode_id), set()
    ) or ArtworkKind.thumbnail.value in artwork.get((OwnerType.show.value, show_id), set())


def episode_blockers(
    episode: Episode,
    show: Show,
    season: Season,
    reference: Reference,
    artwork: dict[tuple[str, int], set[str]],
) -> list[Issue]:
    """Rules an individual episode must satisfy to be published."""
    issues: list[Issue] = []
    label = f"S{season.number}E{episode.number} – {episode.title}"

    if not episode.duration_seconds or episode.duration_seconds <= 0:
        issues.append(
            Issue(
                code="missing_duration",
                severity="blocker",
                message=(
                    f"Episode '{label}' has no duration. Open the episode and enter how long "
                    "it runs, in minutes and seconds."
                ),
                entity_type="episode",
                entity_id=episode.id,
            )
        )

    if not effective_thumbnail(episode.id, show.id, artwork):
        issues.append(
            Issue(
                code="missing_thumbnail",
                severity="blocker",
                message=(
                    f"Episode '{label}' has no thumbnail image. Upload a "
                    f"{reference.spec('thumbnail')['target_px'][0]}x"
                    f"{reference.spec('thumbnail')['target_px'][1]} thumbnail on the episode, "
                    "or add one to the show so all its episodes can use it."
                ),
                entity_type="episode",
                entity_id=episode.id,
            )
        )

    if episode.language not in reference.languages:
        issues.append(
            Issue(
                code="unknown_language",
                severity="blocker",
                message=(
                    f"Episode '{label}' is set to language '{episode.language}', which isn't "
                    f"one of the supported languages ({', '.join(reference.languages)}). "
                    "Pick a supported language."
                ),
                entity_type="episode",
                entity_id=episode.id,
            )
        )

    if episode.seed_issue:
        issues.append(
            Issue(
                code="imported_with_problem",
                severity="blocker",
                message=(
                    f"Episode '{label}' was imported with a problem that needs a human "
                    f"decision: {episode.seed_issue}. Review it, then either fix the episode "
                    "or delete it."
                ),
                entity_type="episode",
                entity_id=episode.id,
                details={"seed_issue": episode.seed_issue},
            )
        )

    return issues


def show_blockers(
    show: Show,
    reference: Reference,
    artwork: dict[tuple[str, int], set[str]],
    publishable_episodes: int,
) -> list[Issue]:
    """Rules a show must satisfy to be published."""
    issues: list[Issue] = []
    present = artwork.get((OwnerType.show.value, show.id), set())

    if not show.section:
        issues.append(
            Issue(
                code="missing_section",
                severity="blocker",
                message=(
                    f"'{show.title}' has no section, so there is nowhere to show it. Choose "
                    f"one of: {', '.join(reference.sections)}."
                ),
                entity_type="show",
                entity_id=show.id,
            )
        )
    elif show.section not in reference.sections:
        issues.append(
            Issue(
                code="unknown_section",
                severity="blocker",
                message=(
                    f"'{show.title}' is in section '{show.section}', which no longer exists. "
                    f"Choose one of: {', '.join(reference.sections)}."
                ),
                entity_type="show",
                entity_id=show.id,
            )
        )

    for kind in (ArtworkKind.poster, ArtworkKind.banner):
        if kind.value not in present:
            w, h = reference.spec(kind.value)["target_px"]
            issues.append(
                Issue(
                    code=f"missing_{kind.value}",
                    severity="blocker",
                    message=(
                        f"'{show.title}' has no {kind.value} image. Upload a {w}x{h} "
                        f"{kind.value} on the show's artwork panel."
                    ),
                    entity_type="show",
                    entity_id=show.id,
                )
            )

    if publishable_episodes == 0:
        issues.append(
            Issue(
                code="no_publishable_episodes",
                severity="blocker",
                message=(
                    f"'{show.title}' has no episodes ready to publish. Fix the problems on "
                    "its episodes, or leave the show as a draft."
                ),
                entity_type="show",
                entity_id=show.id,
            )
        )

    unknown_categories = [c for c in (show.categories or []) if c not in reference.categories]
    if unknown_categories:
        issues.append(
            Issue(
                code="unknown_categories",
                severity="warning",
                message=(
                    f"'{show.title}' uses categories that aren't in the approved list: "
                    f"{', '.join(unknown_categories)}. They won't be searchable until they "
                    "are replaced with approved ones."
                ),
                entity_type="show",
                entity_id=show.id,
                details={"unknown": unknown_categories},
            )
        )

    return issues


def collect_issues(db: Session, reference: Reference) -> dict:
    """Full publish-readiness report, grouped by show.

    Only shows *intended* to be published are gated. A show deliberately left as a draft
    is not a problem to fix — it simply will not appear in the catalogue — so reporting
    it as an error would train editors to ignore the report.
    """
    artwork = _artwork_index(db)

    shows = db.scalars(
        select(Show)
        .options(selectinload(Show.seasons).selectinload(Season.episodes))
        .order_by(Show.title)
    ).all()

    per_show: list[dict] = []
    global_issues: list[Issue] = []
    blocking = False

    for show in shows:
        if show.status is not Status.published:
            continue

        issues: list[Issue] = []
        publishable = 0

        for season in show.seasons:
            for episode in season.episodes:
                if episode.status is not Status.published:
                    # A draft episode is an intentional omission, not an error. Its
                    # problems are still reported if it was quarantined at import.
                    if episode.seed_issue:
                        issues.extend(episode_blockers(episode, show, season, reference, artwork))
                    continue
                ep_issues = episode_blockers(episode, show, season, reference, artwork)
                issues.extend(ep_issues)
                if not any(i.severity == "blocker" for i in ep_issues):
                    publishable += 1

        issues = show_blockers(show, reference, artwork, publishable) + issues

        if issues:
            if any(i.severity == "blocker" for i in issues):
                blocking = True
            per_show.append(
                {
                    "show_id": show.id,
                    "title": show.title,
                    "section": show.section,
                    "blocker_count": sum(1 for i in issues if i.severity == "blocker"),
                    "warning_count": sum(1 for i in issues if i.severity == "warning"),
                    "issues": [i.to_dict() for i in issues],
                }
            )

    # ---- cross-show observations --------------------------------------------------
    # Two shows whose episode titles line up one-for-one in different languages are
    # *probably* language variants that were filed as separate shows. The data does not
    # say so, and merging shows is destructive, so this is raised for a human rather
    # than acted on. (This is what flags peblo-songs vs peblo-songs-lyrical.)
    titles_by_show: dict[int, tuple[str, set[str]]] = {}
    for show in shows:
        titles = {e.title for s in show.seasons for e in s.episodes if s.number != 0}
        if titles:
            titles_by_show[show.id] = (show.title, titles)

    seen: set[tuple[int, int]] = set()
    for a_id, (a_title, a_titles) in titles_by_show.items():
        for b_id, (b_title, b_titles) in titles_by_show.items():
            if a_id >= b_id or (a_id, b_id) in seen or len(a_titles) < 3:
                continue
            seen.add((a_id, b_id))
            overlap = a_titles & b_titles
            if len(overlap) >= min(len(a_titles), len(b_titles)) and len(overlap) >= 3:
                global_issues.append(
                    Issue(
                        code="possible_duplicate_shows",
                        severity="warning",
                        message=(
                            f"'{a_title}' and '{b_title}' have the same {len(overlap)} episode "
                            "titles. They may be language variants of one show that were "
                            "imported separately. If so, give the matching episodes a shared "
                            "content group instead of keeping two shows. Left as-is for now."
                        ),
                        entity_type="show",
                        entity_id=a_id,
                        details={"other_show_id": b_id, "shared_titles": sorted(overlap)},
                    )
                )

    return {
        "blocking_publish": blocking,
        "counts": {
            "shows_with_issues": len(per_show),
            "blockers": sum(s["blocker_count"] for s in per_show),
            "warnings": sum(s["warning_count"] for s in per_show)
            + sum(1 for i in global_issues if i.severity == "warning"),
        },
        "shows": per_show,
        "global_issues": [i.to_dict() for i in global_issues],
    }


def assert_publishable(db: Session, reference: Reference) -> dict:
    """Used by the publish gate. Returns the report; caller raises if blocking."""
    return collect_issues(db, reference)
