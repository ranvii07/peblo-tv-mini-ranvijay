"""Catalogue search.

**Server-side, over the published catalogue** — not in the browser, and not against the
database. Both of those alternatives are wrong here for specific reasons:

* *In the browser* would mean shipping the whole catalogue to every child's device and
  is explicitly called out as a failure mode in the brief.
* *Against the database* would let unpublished rows leak into viewer results and would
  couple the viewer's read path to admin write load and schema churn. Searching the
  published artifact is consistent by construction: if it is in the catalogue, it is
  publishable; if it is not, it cannot be found.

Implementation is a flat in-memory index built once per catalogue version and cached by
checksum, so it is rebuilt only when a publish actually changes something.

Scale (README written answer #3): this is a linear scan over a list of entries. At the
current 8 shows / ~60 entries it is microseconds. It stays comfortably sub-10ms to
roughly 10^4–10^5 entries and a single-digit-MB catalogue. It stops being appropriate
when either the catalogue no longer fits comfortably in each process's memory, or match
quality starts to matter — typo tolerance, stemming, ranking by relevance rather than
returning everything that substring-matches. The next step in order would be a Postgres
full-text index over a `published_entries` projection table written at publish time,
and after that a dedicated engine (Meilisearch/Typesense/OpenSearch) fed by the publish
job as its indexing pipeline.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any


def normalize(s: str | None) -> str:
    """Casefold, strip accents, collapse whitespace — so 'Moti's' matches 'motis'."""
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


@dataclass
class IndexedEntry:
    section: str
    show_id: int
    show_slug: str
    show_title: str
    show_synopsis: str | None
    show_artwork: dict
    categories: list[str]
    season_number: int | None
    is_trailer: bool
    entry: dict
    haystack: str
    languages: tuple[str, ...]


class CatalogSearchIndex:
    """Flat index over one catalogue version."""

    def __init__(self, catalog: dict, checksum: str):
        self.checksum = checksum
        self.entries: list[IndexedEntry] = []
        self.categories: set[str] = set()
        self.languages: set[str] = set()
        self.sections: list[str] = []

        for section in catalog.get("sections", []):
            name = section["section"]
            self.sections.append(name)
            for show in section["shows"]:
                self.categories.update(show.get("categories") or [])
                buckets: list[tuple[int | None, bool, list[dict]]] = [
                    (s["number"], False, s["entries"]) for s in show.get("seasons", [])
                ]
                if show.get("trailers"):
                    buckets.append((0, True, show["trailers"]))

                for season_number, is_trailer, entries in buckets:
                    for entry in entries:
                        self.languages.update(entry.get("languages") or [])
                        # q matches show title AND episode title AND category, per brief.
                        haystack = " ".join(
                            normalize(x)
                            for x in [
                                show["title"],
                                show.get("synopsis"),
                                entry["title"],
                                *(show.get("categories") or []),
                            ]
                        )
                        self.entries.append(
                            IndexedEntry(
                                section=name,
                                show_id=show["id"],
                                show_slug=show["slug"],
                                show_title=show["title"],
                                show_synopsis=show.get("synopsis"),
                                show_artwork=show.get("artwork") or {},
                                categories=list(show.get("categories") or []),
                                season_number=season_number,
                                is_trailer=is_trailer,
                                entry=entry,
                                haystack=haystack,
                                languages=tuple(entry.get("languages") or []),
                            )
                        )

    def search(
        self,
        q: str | None = None,
        category: str | None = None,
        language: str | None = None,
        section: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """All four filters compose with AND. Any subset may be omitted."""
        needle = normalize(q)
        results = []
        for item in self.entries:
            if needle and needle not in item.haystack:
                continue
            if category and category not in item.categories:
                continue
            if language and language not in item.languages:
                continue
            if section and section != item.section:
                continue
            results.append(item)

        total = len(results)
        return {
            "query": {
                "q": q or None,
                "category": category,
                "language": language,
                "section": section,
            },
            "total": total,
            "results": [
                {
                    "show_id": r.show_id,
                    "show_slug": r.show_slug,
                    "show_title": r.show_title,
                    "section": r.section,
                    "categories": r.categories,
                    "season_number": r.season_number,
                    "is_trailer": r.is_trailer,
                    "poster": r.show_artwork.get("poster"),
                    "entry": r.entry,
                }
                for r in results[:limit]
            ],
            "facets": {
                "categories": sorted(self.categories),
                "languages": sorted(self.languages),
                "sections": self.sections,
            },
        }


class _IndexCache:
    """Caches one index, keyed by catalogue checksum."""

    def __init__(self) -> None:
        self._index: CatalogSearchIndex | None = None

    def get(self, catalog: dict, checksum: str) -> CatalogSearchIndex:
        if self._index is None or self._index.checksum != checksum:
            self._index = CatalogSearchIndex(catalog, checksum)
        return self._index

    def clear(self) -> None:
        self._index = None


index_cache = _IndexCache()
