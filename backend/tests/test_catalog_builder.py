"""Tests for the catalogue builder.

This is where the testing effort is concentrated: language grouping, Season 0 handling
and determinism are the three things the publish job is graded on, and all three live in
a pure function that needs no database to exercise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Reference
from app.services.catalog_builder import (
    build_catalog,
    canonical_bytes,
    catalog_checksum,
)

REFERENCE = Reference(
    json.loads(
        (Path(__file__).resolve().parents[2] / "data" / "seed" / "reference.json").read_text(
            encoding="utf-8"
        )
    )
)


def ep(id, number, title, language, content_group=None, duration=300, thumbnail="t.jpg"):
    return {
        "id": id,
        "number": number,
        "title": title,
        "synopsis": None,
        "duration_seconds": duration,
        "language": language,
        "content_group": content_group,
        "thumbnail": thumbnail,
    }


def show(id, slug, title, section, seasons, featured=False, categories=None):
    return {
        "id": id,
        "slug": slug,
        "title": title,
        "synopsis": "syn",
        "section": section,
        "categories": categories or ["stories"],
        "featured": featured,
        "artwork": {"poster": "p.jpg", "banner": "b.jpg"},
        "seasons": seasons,
    }


def season(number, episodes, title=None):
    return {"number": number, "title": title, "episodes": episodes}


def build(snapshot, run_id=1, generated_at="2026-01-01T00:00:00Z"):
    return build_catalog(snapshot, REFERENCE, run_id=run_id, generated_at=generated_at)


class TestLanguageGrouping:
    def test_variants_collapse_into_one_entry_listing_languages(self):
        snap = {
            "shows": [
                show(1, "s", "Show", "series", [
                    season(1, [
                        ep(10, 1, "The Lost Kite", "en", "cg-s01e01"),
                        ep(11, 1, "Patang", "hi", "cg-s01e01"),
                    ])
                ])
            ]
        }
        entries = build(snap)["sections"][0]["shows"][0]["seasons"][0]["entries"]
        assert len(entries) == 1, "two language variants must collapse to one entry"
        assert entries[0]["languages"] == ["en", "hi"]
        assert set(entries[0]["variants"]) == {"en", "hi"}

    def test_english_is_canonical_when_present(self):
        snap = {"shows": [show(1, "s", "Show", "series", [season(1, [
            ep(11, 1, "Patang", "hi", "cg"),
            ep(10, 1, "The Lost Kite", "en", "cg"),
        ])])]}
        entry = build(snap)["sections"][0]["shows"][0]["seasons"][0]["entries"][0]
        assert entry["title"] == "The Lost Kite"
        assert entry["entry_id"] == 10

    def test_canonical_falls_back_to_first_language_alphabetically(self):
        snap = {"shows": [show(1, "s", "Show", "series", [season(1, [
            ep(11, 1, "Hindi title", "hi", "cg"),
        ])])]}
        entry = build(snap)["sections"][0]["shows"][0]["seasons"][0]["entries"][0]
        assert entry["title"] == "Hindi title"
        assert entry["languages"] == ["hi"]

    def test_null_content_group_episodes_stay_separate(self):
        """A NULL content_group is not a group — these must not merge into one entry."""
        snap = {"shows": [show(1, "s", "Show", "series", [season(1, [
            ep(10, 1, "One", "en", None),
            ep(11, 2, "Two", "en", None),
        ])])]}
        entries = build(snap)["sections"][0]["shows"][0]["seasons"][0]["entries"]
        assert len(entries) == 2
        assert [e["languages"] for e in entries] == [["en"], ["en"]]

    def test_per_language_duration_preserved_in_variants(self):
        snap = {"shows": [show(1, "s", "Show", "series", [season(1, [
            ep(10, 1, "T", "en", "cg", duration=540),
            ep(11, 1, "T-hi", "hi", "cg", duration=660),
        ])])]}
        entry = build(snap)["sections"][0]["shows"][0]["seasons"][0]["entries"][0]
        assert entry["variants"]["en"]["duration_seconds"] == 540
        assert entry["variants"]["hi"]["duration_seconds"] == 660
        assert entry["duration_seconds"] == 540, "top level reflects canonical variant"


class TestSeasonZero:
    def test_season_zero_becomes_trailers_not_a_season(self):
        snap = {"shows": [show(1, "s", "Show", "series", [
            season(0, [ep(90, 1, "Trailer", "en", "cg-trailer")]),
            season(1, [ep(10, 1, "Real episode", "en", "cg-s01e01")]),
        ])]}
        out_show = build(snap)["sections"][0]["shows"][0]
        assert [s["number"] for s in out_show["seasons"]] == [1]
        assert len(out_show["trailers"]) == 1
        assert out_show["trailers"][0]["title"] == "Trailer"

    def test_show_with_only_trailers_still_appears(self):
        snap = {"shows": [show(1, "s", "Show", "series", [
            season(0, [ep(90, 1, "Trailer", "en")]),
        ])]}
        out_show = build(snap)["sections"][0]["shows"][0]
        assert out_show["seasons"] == []
        assert len(out_show["trailers"]) == 1

    def test_show_with_no_episodes_at_all_is_omitted(self):
        snap = {"shows": [show(1, "s", "Show", "series", [season(1, [])])]}
        assert build(snap)["sections"] == []


class TestOrdering:
    def test_sections_follow_reference_order_not_alphabetical(self):
        snap = {"shows": [
            show(1, "a", "A", "songs", [season(1, [ep(1, 1, "x", "en")])]),
            show(2, "b", "B", "featured", [season(1, [ep(2, 1, "y", "en")])]),
            show(3, "c", "C", "series", [season(1, [ep(3, 1, "z", "en")])]),
        ]}
        got = [s["section"] for s in build(snap)["sections"]]
        assert got == ["featured", "series", "songs"], "reference.json order, not sorted()"

    def test_shows_alphabetical_within_section(self):
        snap = {"shows": [
            show(1, "z", "Zebra", "series", [season(1, [ep(1, 1, "x", "en")])]),
            show(2, "a", "Apple", "series", [season(1, [ep(2, 1, "y", "en")])]),
        ]}
        titles = [s["title"] for s in build(snap)["sections"][0]["shows"]]
        assert titles == ["Apple", "Zebra"]

    def test_entries_ordered_by_episode_number(self):
        snap = {"shows": [show(1, "s", "S", "series", [season(1, [
            ep(3, 3, "Third", "en"), ep(1, 1, "First", "en"), ep(2, 2, "Second", "en"),
        ])])]}
        entries = build(snap)["sections"][0]["shows"][0]["seasons"][0]["entries"]
        assert [e["episode_number"] for e in entries] == [1, 2, 3]


class TestDeterminism:
    def _snapshot(self):
        return {"shows": [
            show(2, "b", "Beta", "series", [
                season(0, [ep(80, 1, "Trailer", "en", "cgt")]),
                season(1, [ep(20, 1, "B1", "en", "cg20"), ep(21, 1, "B1-hi", "hi", "cg20")]),
            ]),
            show(1, "a", "Alpha", "featured", [season(1, [ep(10, 1, "A1", "en")])]),
        ]}

    def test_same_content_produces_identical_bytes(self):
        a = build(self._snapshot())
        b = build(self._snapshot())
        assert canonical_bytes(a) == canonical_bytes(b)

    def test_input_order_does_not_change_output(self):
        snap = self._snapshot()
        reversed_snap = {"shows": list(reversed(snap["shows"]))}
        for s in reversed_snap["shows"]:
            s["seasons"] = list(reversed(s["seasons"]))
            for se in s["seasons"]:
                se["episodes"] = list(reversed(se["episodes"]))
        assert canonical_bytes(build(snap)) == canonical_bytes(build(reversed_snap))

    def test_checksum_ignores_run_id_and_timestamp(self):
        """Otherwise republishing unchanged content could never be a no-op."""
        a = build(self._snapshot(), run_id=1, generated_at="2026-01-01T00:00:00Z")
        b = build(self._snapshot(), run_id=99, generated_at="2027-06-06T12:00:00Z")
        assert catalog_checksum(a) == catalog_checksum(b)
        assert canonical_bytes(a) != canonical_bytes(b), "the documents do differ"

    def test_checksum_changes_when_content_changes(self):
        a = build(self._snapshot())
        snap = self._snapshot()
        snap["shows"][1]["title"] = "Alpha renamed"
        assert catalog_checksum(a) != catalog_checksum(build(snap))


class TestCounts:
    def test_counts_report_entries_and_episodes_separately(self):
        """95 episodes collapsing into fewer entries is the whole point of grouping."""
        snap = {"shows": [show(1, "s", "S", "series", [season(1, [
            ep(10, 1, "T", "en", "cg1"), ep(11, 1, "T-hi", "hi", "cg1"),
            ep(12, 2, "U", "en", "cg2"),
        ])])]}
        counts = build(snap)["counts"]
        assert counts["episodes"] == 3
        assert counts["entries"] == 2
        assert counts["shows"] == 1
        assert counts["languages"] == 2


@pytest.mark.parametrize("missing", ["thumbnail", "duration_seconds"])
def test_optional_episode_fields_tolerated(missing):
    """The builder consumes already-validated data, but must not crash on NULLs."""
    e = ep(10, 1, "T", "en")
    e[missing] = None
    snap = {"shows": [show(1, "s", "S", "series", [season(1, [e])])]}
    entry = build(snap)["sections"][0]["shows"][0]["seasons"][0]["entries"][0]
    assert entry[missing] is None
