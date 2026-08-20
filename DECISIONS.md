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

---

## Final review pass — re-reading the brief against the built system

### D-012 · `reference.json` is served to the CMS, not restated in it
The rule from the start was that nothing hardcodes reference values. The backend honoured
it; the CMS did not — it carried its own copies of the section list, the language list and
the three artwork specs, under a comment claiming they came from the API. Three copies of
the truth, one of them a lie in a code comment.

**Decision:** added `GET /api/reference` (signed-in; it is CMS configuration, not public
data) and the CMS now reads sections, categories, languages and artwork specs from it. The
artwork slots render their required dimensions from the same source that validates the
upload, so the label and the rule can never drift apart. Adding a language is a change to
one JSON file.

### D-013 · Draft shows appear in the validation report as warnings
`rhyme-rangers` ships with `section: null` on all 8 rows, which leaves it a draft. The
report only listed *published* shows, on the reasoning that a draft cannot block a publish
it is not part of — correct, but it meant a planted defect was invisible on the page whose
job is to surface planted defects.

**Decision:** draft shows are listed too, with every issue downgraded to `warning` and the
group tagged `draft`. `blocking_publish` is unchanged, so a deliberate draft still cannot
block anyone; an editor can see exactly what it would take to bring it live. Reporting them
as errors would have trained editors to ignore the report; reporting nothing hid the
defect. Warnings are the honest middle.

### D-014 · The publish advisory lock needs its own connection
`pg_try_advisory_lock` is *session*-scoped — it belongs to the connection that took it. The
publish job commits three times, and a SQLAlchemy `Session` returns its connection to the
pool on every commit. So the lock could be taken on one pooled connection and released
against another, leaving the real lock held until that connection was recycled — after
which every publish would 409 forever.

**Decision:** the lock is taken and released on a connection checked out explicitly for the
job and closed in a `finally`. Same semantics, no dependence on pool behaviour.

### D-015 · The CMS needed create, not just edit
The brief asks for a "create/edit form". The API had `POST /shows`, `/shows/{id}/seasons`
and `/seasons/{id}/episodes` from the start, and the tests covered them, but the CMS only
ever issued PATCHes — an editor could fix a show but never start one. Added a **New show**
page and inline **add season** / **add episode** controls on the show editor, plus category
editing on the details form (categories drive the viewer's filters, so an editor who cannot
set them cannot control where a show appears). New shows and episodes start as drafts and
go through the same publish gate as everything else.

### D-016 · `seed_issue` must clear on the way *into* the publish gate, not after it
Found by running the fix-and-publish loop end to end in Docker rather than reading the
code. `episodes.seed_issue` marks a row that arrived broken and needs a human decision,
and the validation report raises it as a blocker. The gate cleared the flag only *after*
the row passed — but the flag was itself one of the things the gate checked. So a flagged
row could never be published: uploading the missing thumbnail resolved the real problem
and the row stayed blocked, on the flag alone. The message told editors to "fix it or
delete it" and only delete actually worked.

**Decision:** the flag is cleared before the gate evaluates. Asking to publish a row *is*
the human decision the flag was waiting for. The problems it pointed at — missing
thumbnail, missing duration, unsupported language — are separate rules and still apply,
so nothing broken gets published; only the "someone needs to look at this" marker is
resolved by looking. Covered by a test that walks the whole loop: refused → upload the
thumbnail → published → appears in the catalogue.

Two things this exposed by association, both fixed: the report told editors to add a
thumbnail *to the show* as a fallback, and the CMS had no show-level thumbnail slot; and
it told them to delete a bad row, and the CMS had no delete control. An error message
that names an action the UI cannot perform is worse than no message.

### D-017 · Episodes do not inherit the show's synopsis
`seed_shows.json` carries one `synopsis` per row, and it is identical on every row of a
show — the ingest already treats disagreement across a show's rows as a finding, which
is only meaningful if the field is show-level. Ingest was nonetheless copying it onto
each episode, so the viewer printed the same paragraph under all ten episodes of a show.

**Decision:** episodes arrive with no synopsis. The seed does not contain episode
synopses, and manufacturing one by duplicating the show's blurb is inventing data — the
same failure mode as silently repairing a bad row, just quieter. The column stays, for
episodes given a real synopsis through the CMS.

### D-018 · The test suite refuses any database not named `*_test`
Found the hard way: following the README's own local-test instructions
(`DATABASE_URL=...localhost:5432/peblo pytest`) against the running stack wiped it. The
integration fixtures rebuild the schema with `DROP SCHEMA public CASCADE`, and the
documented command pointed them at the live database. The containerised path had already
been made safe; the local path had not, so the footgun survived in prose.

**Decision:** `conftest.py` checks the database name and treats anything not ending in
`_test` as "no usable test database" — the integration tests skip with an explanation
instead of running. Skip rather than error, so the pure-function suites still run
anywhere with no database at all. Documentation that says "be careful" is not a safety
mechanism; a check is. The README command now names `peblo_test`.
