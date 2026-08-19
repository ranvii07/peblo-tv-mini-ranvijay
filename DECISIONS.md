# DECISIONS.md — running log

Chronological record of every judgment call. Folded into the README at the end.

---

## Phase 0 — seed data acquisition & audit

### D-001 · The `assets/` were not where the brief's file table implied
The challenge PDF's three prose links point to `CHALLENGE.md`, `reference.json`, and
`seed_shows.json`. The six `assets/` images are **separate hyperlinks embedded in the PDF**,
not listed in the file table. Extracted them from the PDF's link annotations.
All six recovered: `poster_good.jpg`, `poster_wrong_ratio.jpg`, `banner_good.jpg`,
`banner_too_big.png`, `thumb_good.jpg`, `thumb_tiny.jpg`.

### D-002 · Artwork validation must key on exact dimensions, not a size ceiling or a ratio band
Measured the provided assets before writing the validator:

| asset | dimensions | ratio | bytes | verdict |
|---|---|---|---|---|
| poster_good.jpg | 600x900 | 0.667 (2:3) | 9.1 KB | accept |
| banner_good.jpg | 1280x720 | 1.778 (16:9) | 14.7 KB | accept |
| thumb_good.jpg | 640x360 | 1.778 (16:9) | 4.2 KB | accept |
| poster_wrong_ratio.jpg | 900x600 | 1.500 | 9.1 KB | reject — ratio transposed |
| banner_too_big.png | 2560x1440 | 1.778 (16:9) | 13.8 KB | reject — 2x target_px |
| thumb_tiny.jpg | 160x90 | 1.778 (16:9) | 0.8 KB | reject — 0.25x target_px |

Two consequences, both of which contradict the blueprint's provisional rule:
1. **The 200 KB ceiling catches none of them** — every asset is under 15 KB. Size is a real
   rule from `reference.json` and is still enforced, but it is not what the fixtures test.
2. `banner_too_big` and `thumb_tiny` are both *exactly the right aspect ratio*. The blueprint
   proposed accepting "spec minimum to 2x spec"; that would have **wrongly accepted
   banner_too_big** (2560x1440 is exactly 2x). Rejected that rule.

**Decision:** require dimensions to equal `reference.json`'s `target_px` exactly. Aspect ratio
is still computed first, so a transposed image gets a "wrong shape" message rather than a
less helpful "wrong size" one. Three distinct error codes: `wrong_aspect_ratio`,
`wrong_dimensions`, `file_too_large`.

### D-003 · Seed shape is a flat, denormalized episode list — shows are implied
`seed_shows.json` is 95 episode rows, not a nested show tree. Show identity is the `slug`
column, with `show_title`/`section`/`categories`/`synopsis` repeated on every row of that show.
Ingest therefore derives 8 shows and their seasons from the row set rather than reading a
show object. Where repeated show-level fields disagree across rows, that is itself a finding.

### D-004 · `artwork_available` declares intent, not files
Rows carry `artwork_available: ["poster","banner","thumbnail"]` — a list of which artwork
*should* exist, with no file reference. The seeder therefore attaches the three known-good
sample images to satisfy each declared slot, so `docker compose up` yields a viewer with real
images. A row declaring fewer slots genuinely lacks that artwork and is a publish blocker.
Poster/banner are attached to the **show**, thumbnail to the **episode** — matching viewer usage.

### D-005 · Seed imperfections found (surfaced, never silently repaired)
1. **`ep_9001`** — duplicate `(content_group, language)` = `(motis-many-lives-s01e02, hi)`,
   colliding with `ep_0004`. Its `episode_title` is `"The Lost Kite (v2)"` while the rest of
   that content_group is `"Rain on the Roof"` — a bad re-upload that also collides on
   `(season, episode_number)`. Ingested but quarantined as `draft` with a blocker issue.
2. **`rhyme-rangers`** — all 8 rows have `section: null`. A published show requires a section,
   so the whole show is blocked until an editor picks one.
3. **`ep_0036`** — `status: "published"` in the seed but `artwork_available: []`, so it has no
   thumbnail. Ingested as draft-with-blocker; publishing it is refused until artwork exists.
4. **Season 0 trailers** (`ep_0093`, `ep_0094`) carry `artwork_available: ["thumbnail"]` only.
   Correct and expected — so the poster/banner requirement is a **show**-level rule and must
   not be applied to Season 0 entries.
5. **`peblo-songs-lyrical`** repeats every `peblo-songs` episode title in `en` only, under
   *distinct* content_groups. This looks like language variants filed as a separate show, but
   the data does not say so. **Not merged** — surfaced as a warning for a human to decide.
   Silently merging two shows would be exactly the "helpfully cleaned the data" failure.
6. Clean: no invalid languages, categories, or durations (all 75-660s), no orphan seasons.

### D-006 · Repo lives outside the Obsidian vault
Built at `C:\Users\Dell\peblo-tv-mini` as its own git repo. The vault is an LLM-maintained
knowledge base with unrelated conventions; a deliverable repo does not belong inside it.

---

## Phase 1 — environment

### D-007 · Docker was not installed on the build machine
Neither Docker nor WSL was present. Since `docker compose up` working is an explicit graded
pass/fail item, this was escalated rather than worked around.

---

## Phase 2/4 — schema, storage, validation

### D-008 · Catalogue checksum excludes `run_id` and `generated_at`
Idempotency is defined as "republishing unchanged content changes nothing". If the
checksum covered the generation timestamp and run id — both of which change by
construction on every run — no republish could ever be detected as a no-op and the
requirement would be unsatisfiable. The checksum therefore covers content only, while
the stored document still carries both fields for provenance.

### D-009 · `build_catalog` is a pure function
Snapshot in, dict out; no DB, no clock, no storage. Language grouping, Season 0 and
deterministic ordering are the riskiest logic in the system, so they are the part that
must be testable without infrastructure. All stateful concerns live in the publish
service. 18 of the tests target this function alone.

### D-010 · A NULL `content_group` is not a group
Grouping keys on `("cg", content_group)` when set and `("ep", id)` otherwise. Keying all
NULLs together would collapse every ungrouped episode in a season into a single entry —
a subtle and very destructive bug. Explicitly tested.

### D-011 · Determinism is enforced by total orderings, not incidental sort stability
Every list sorts by a tuple that cannot tie: entries by `(episode_number, title,
entry_id)`, shows by `(title, id)`, sections by reference.json position. Tested by
building from a reversed snapshot and asserting byte-identical output.
