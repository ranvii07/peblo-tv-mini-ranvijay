"""Builds the published catalogue document.

`build_catalog` is a **pure function**: snapshot in, catalogue dict out, no database, no
clock, no storage. That is deliberate — it is the highest-risk logic in the system
(language grouping, Season 0, deterministic ordering), so it is also the part that must
be trivially testable. Everything stateful lives in `publish_service`.

Two conventions from `reference.json` are implemented here:

* **Season 0 is trailers.** Season 0 never appears in `seasons`; its entries go to the
  show's `trailers` array. The viewer therefore cannot accidentally render it as a
  normal season.
* **`content_group` collapses language variants.** Episodes sharing a `content_group`
  become ONE entry that lists its available languages, with per-language metadata under
  `variants`. Episodes with no `content_group` are singleton entries.

Determinism matters because the publish job is idempotent by checksum: building the same
content twice must produce byte-identical JSON, or every republish would look like a
change. Every list is explicitly sorted by a total order, and serialization uses
`sort_keys=True`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

CATALOG_VERSION = 1


def _canonical_variant(variants: list[dict]) -> dict:
    """Pick the variant whose metadata represents the entry.

    English if present, otherwise the first language alphabetically. Deterministic
    either way, which is what the checksum depends on.
    """
    by_lang = {v["language"]: v for v in variants}
    if "en" in by_lang:
        return by_lang["en"]
    return by_lang[sorted(by_lang)[0]]


def _build_entries(episodes: list[dict]) -> list[dict]:
    """Collapse a season's episodes into catalogue entries.

    Grouping key is `content_group` when set. A NULL content_group is *not* a group —
    each such episode stands alone — so it is keyed by its own id to keep them distinct.
    """
    groups: dict[Any, list[dict]] = {}
    for ep in episodes:
        key = ("cg", ep["content_group"]) if ep.get("content_group") else ("ep", ep["id"])
        groups.setdefault(key, []).append(ep)

    entries: list[dict] = []
    for members in groups.values():
        variants_sorted = sorted(members, key=lambda e: e["language"])
        canonical = _canonical_variant(variants_sorted)
        entries.append(
            {
                "entry_id": canonical["id"],
                "content_group": canonical.get("content_group"),
                "title": canonical["title"],
                "synopsis": canonical.get("synopsis"),
                "episode_number": canonical["number"],
                "languages": sorted({v["language"] for v in variants_sorted}),
                "duration_seconds": canonical.get("duration_seconds"),
                "thumbnail": canonical.get("thumbnail"),
                "variants": {
                    v["language"]: {
                        "episode_id": v["id"],
                        "title": v["title"],
                        "duration_seconds": v.get("duration_seconds"),
                        "thumbnail": v.get("thumbnail"),
                    }
                    for v in variants_sorted
                },
            }
        )

    # Total order: episode number, then title, then entry id — no ties possible.
    entries.sort(key=lambda e: (e["episode_number"], e["title"], e["entry_id"]))
    return entries


def build_catalog(
    snapshot: dict,
    reference: Any,
    *,
    run_id: int,
    generated_at: str,
) -> dict:
    """Build the catalogue document from a snapshot of published content.

    The snapshot is expected to contain only already-published shows and episodes;
    filtering is the caller's job (it happens in SQL, where it belongs). We assert the
    invariant rather than re-filtering silently.
    """
    sections: dict[str, list[dict]] = {}

    total_shows = 0
    total_entries = 0
    total_episodes = 0
    languages_seen: set[str] = set()

    for show in snapshot["shows"]:
        seasons_out: list[dict] = []
        trailers_out: list[dict] = []

        for season in sorted(show["seasons"], key=lambda s: s["number"]):
            episodes = season["episodes"]
            if not episodes:
                continue
            entries = _build_entries(episodes)
            total_episodes += len(episodes)
            for ep in episodes:
                languages_seen.add(ep["language"])

            if season["number"] == 0:
                # Season 0 is trailers: flattened onto the show, never a season.
                trailers_out.extend(entries)
            else:
                seasons_out.append(
                    {
                        "number": season["number"],
                        "title": season.get("title"),
                        "entries": entries,
                    }
                )
            total_entries += len(entries)

        # A show with no publishable content contributes nothing to the catalogue.
        if not seasons_out and not trailers_out:
            continue

        total_shows += 1
        section = show["section"]
        sections.setdefault(section, []).append(
            {
                "id": show["id"],
                "slug": show["slug"],
                "title": show["title"],
                "synopsis": show.get("synopsis"),
                "categories": sorted(show.get("categories") or []),
                "featured": bool(show.get("featured")),
                "artwork": show.get("artwork") or {},
                "seasons": seasons_out,
                "trailers": trailers_out,
            }
        )

    # Sections appear in reference.json's order; shows alphabetically within a section.
    sections_out = []
    for name in sorted(sections, key=lambda s: (reference.section_order(s), s)):
        shows_in = sorted(sections[name], key=lambda sh: (sh["title"], sh["id"]))
        sections_out.append({"section": name, "shows": shows_in})

    return {
        "catalog_version": CATALOG_VERSION,
        "generated_at": generated_at,
        "run_id": run_id,
        "counts": {
            "shows": total_shows,
            "entries": total_entries,
            "episodes": total_episodes,
            "languages": len(languages_seen),
        },
        "sections": sections_out,
    }


def canonical_bytes(catalog: dict) -> bytes:
    """Serialize deterministically. Same content in, same bytes out, always."""
    return json.dumps(catalog, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def catalog_checksum(catalog: dict) -> str:
    """Checksum over content only.

    `generated_at` and `run_id` change on every run by construction, so they are
    excluded — otherwise no republish could ever be detected as a no-op, and the
    idempotency requirement would be unsatisfiable.
    """
    content = {k: v for k, v in catalog.items() if k not in ("generated_at", "run_id")}
    return hashlib.sha256(canonical_bytes(content)).hexdigest()
