# Peblo TV Mini

CMS upload → published catalogue → Netflix-style browse.

A content editor manages shows, episodes and artwork in an internal CMS. An admin
publishes, which builds an immutable catalogue file and flips a pointer at it. A
separate viewer app reads only that published file.

```
   ┌──────────────┐        ┌───────────────────────────┐
   │  CMS (React) │───────▶│  API (FastAPI + Postgres) │
   │  :3000       │  auth  │                           │
   └──────────────┘        │  POST /admin/catalog/     │
                           │       publish             │
                           │         │                 │
                           │         ▼                 │
                           │  build_catalog()  (pure)  │
                           │         │                 │
                           │         ▼                 │
                           │   storage.put(            │
                           │     catalogs/{run}.json)  │  ← new key, never overwritten
                           │         │                 │
                           │         ▼                 │
                           │   flip is_current pointer │  ← one transaction
                           └───────────┬───────────────┘
                                       │
   ┌──────────────┐   GET /catalog     │
   │ Viewer(React)│◀───────────────────┘
   │  :3001       │   GET /catalog/search
   └──────────────┘   (public, no auth)
```

---

## 1. How to run it

**Requirements:** Docker with Compose v2. Nothing else — no `.env` file needed.

```bash
docker compose up --build
```

Then open:

| What | URL |
|---|---|
| Viewer (public browse) | http://localhost:3001 |
| CMS (internal) | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| Health | http://localhost:8000/api/health |

**Seeded logins** (printed on the CMS login page too, for convenience):

| Role | Email | Password |
|---|---|---|
| admin — CRUD + publish | `admin@peblo.test` | `admin123` |
| editor — CRUD only | `editor@peblo.test` | `editor123` |

On first boot the API runs migrations, ingests the 95 seed episodes, attaches artwork,
and **publishes once** so the viewer has content immediately rather than showing an
empty state that looks like a bug. All of it is idempotent: `docker compose up` again
changes nothing.

To start completely fresh: `docker compose down -v && docker compose up --build`.

### Running the tests

```bash
docker compose --profile test run --rm --build test
```

`--build` matters: `run` reuses an existing image, so without it a code change would be
tested against the previous build.

70 tests, all against a **real Postgres** — the schema leans on partial unique indexes,
array columns and advisory locks, so testing it against SQLite would prove nothing about
what actually runs.

The suite rebuilds the schema with `DROP SCHEMA public CASCADE`, so it deliberately
targets a separate `peblo_test` database (created on first use) rather than the one the
running stack is serving from. That is why it is a profile-gated service and not
`docker compose exec api pytest`.

Or locally, against a Postgres you already have — note the **`peblo_test`** database:

```bash
cd backend
pip install -r requirements-dev.txt
DATABASE_URL=postgresql+psycopg://peblo:peblo@localhost:5432/peblo_test pytest -q
```

The integration tests run `DROP SCHEMA public CASCADE`, so `conftest.py` refuses any
database whose name does not end in `_test` — including the `peblo` database the running
stack serves from. It skips with an explanation rather than erroring, so the
pure-function tests (catalogue builder, artwork validator) still run with no database at
all. That guard exists because the earlier version of this paragraph named the live
database, and following my own instructions wiped a running stack.

---

## 2. What I found in the seed data

The brief says the data is deliberately imperfect and that finding it is part of the
exercise. **Nothing here was silently repaired.** Everything loads as-is; anything
invalid is quarantined as `draft` with the reason recorded on the row, and the
validation report shows an editor what to fix and how.

| # | Finding | How it's handled |
|---|---|---|
| 1 | **`ep_9001` duplicates `(content_group, language)`** — it claims `motis-many-lives-s01e02` / `hi`, which `ep_0004` already owns. Its title is `"The Lost Kite (v2)"` while the rest of that group is `"Rain on the Roof"`, and it also collides on `(season, episode_number)`. A bad re-upload. | Row is **kept** (deleting it would destroy the evidence), forced to `draft`, its `content_group` detached so the partial unique index holds, and `seed_issue` records the collision. Appears in the report as a blocker naming both episodes. |
| 2 | **`rhyme-rangers` has `section: null`** on all 8 rows. | Show loads with a NULL section and stays `draft`. It is listed in the validation report — tagged `draft`, with "choose one of: featured, series, minisodes, songs" as a **warning**, since a draft cannot block a publish it isn't part of. The CMS shows `— none —` in red on the list and inline on the editor. |
| 3 | **`ep_0036` is `published` with `artwork_available: []`** — no thumbnail. | Quarantined as `draft`. Publishing it is refused until artwork exists. |
| 4 | **Season 0 trailers** (`ep_0093`, `ep_0094`) have thumbnail-only artwork. | Correct, not a defect — but it means the poster/banner requirement is a **show**-level rule that must not be applied to Season 0 entries. The rules are written accordingly. |
| 5 | **`peblo-songs-lyrical` mirrors every `peblo-songs` episode title** in `en` only, under *distinct* content_groups. Looks like language variants filed as a separate show. | **Not merged.** The data doesn't actually say they're the same show, and silently merging two shows is exactly the "helpfully cleaned your data" failure. Raised as a `warning` for a human to decide, with the overlapping titles listed. |
| 6 | Clean: no invalid languages, categories or sections beyond the above; all durations present and positive (75–660s); no orphan seasons. | — |

Two subtleties worth stating. First, the seed's `synopsis` is a **show** field — it
repeats identically on all rows of a show — so episodes are ingested without one rather
than each inheriting a copy. Manufacturing an episode synopsis by duplicating the show's
blurb is inventing data, and the viewer would print the same paragraph under all ten
episodes.

Second, the seed's `artwork_available` field declares *which* artwork
should exist but ships no per-show image files. The seeder attaches the three known-good
sample images to satisfy each declared slot, so the viewer has real pictures. A row
declaring fewer slots genuinely lacks that artwork and is treated as a blocker.

### The artwork fixtures decided a design rule

I measured the six supplied assets before writing the validator:

| asset | dimensions | ratio | size | verdict |
|---|---|---|---|---|
| `poster_good.jpg` | 600×900 | 2:3 | 9.1 KB | accept |
| `banner_good.jpg` | 1280×720 | 16:9 | 14.7 KB | accept |
| `thumb_good.jpg` | 640×360 | 16:9 | 4.2 KB | accept |
| `poster_wrong_ratio.jpg` | 900×600 | 3:2 | 9.1 KB | reject — shape |
| `banner_too_big.png` | 2560×1440 | **16:9** | 13.8 KB | reject — size |
| `thumb_tiny.jpg` | 160×90 | **16:9** | 0.8 KB | reject — size |

Two of the three bad files have a **correct aspect ratio** and all six are **far under
the 200 KB ceiling**. So neither a ratio check nor a byte-size check rejects
`banner_too_big` or `thumb_tiny` — only an exact dimension check does. My original plan
allowed "spec minimum up to 2× spec", which would have *accepted* `banner_too_big` at
exactly 2×. I rejected that rule and require dimensions to equal `target_px` exactly.
The 200 KB rule is still enforced; it just isn't what these fixtures test.

Aspect ratio is still checked *first*, so a transposed image is told it's the wrong
**shape** (actionable) rather than merely the wrong size.

---

## 3. Decisions and trade-offs

The full running log is in [DECISIONS.md](DECISIONS.md). The ones that matter:

**Schema tolerates bad data; the publish gate enforces validity.** `shows.section` and
`episodes.duration_seconds` are nullable. A schema strict enough to reject the seed
would have hidden the very problems the exercise is about. The one exception is
`(content_group, language)`, which is a real partial unique index because the brief
states it as a hard rule — so the colliding row is quarantined rather than dropped.

**`build_catalog` is a pure function.** Snapshot in, dict out; no DB, no clock, no
storage. Language grouping, Season 0 and deterministic ordering are the riskiest logic
in the system, so they are also the part that needs no infrastructure to test. 18 tests
target this function alone. Everything stateful lives in `publish_service`.

**The checksum covers content only,** excluding `run_id` and `generated_at`. Both change
on every run by construction, so including them would make "republish unchanged content
is a no-op" impossible to satisfy.

**A NULL `content_group` is not a group.** Grouping keys on `("cg", value)` when set and
`("ep", id)` otherwise. Keying all NULLs together would silently collapse every
ungrouped episode in a season into one entry — a quiet, destructive bug. Explicitly
tested.

**`reference.json` is served, never restated.** Sections, categories, languages and the
three artwork specs are loaded from that file at runtime and handed to the CMS by
`GET /api/reference`. The artwork slot's "600×900 (2:3), max 200 KB" label and the
server-side rule that enforces it read the same source, so the label and the rule cannot
drift apart. Adding a language is a one-file data change, not two deploys.

**Two separate frontend apps, not one app with routes.** The rubric penalises the viewer
touching admin endpoints, so the separation is structural rather than a matter of
discipline: the viewer bundle contains no auth code and its entire network surface is
one file with two `fetch` calls.

**Synchronous publish, no job queue.** 95 episodes build in well under a second. Celery
or RQ would add a broker, a worker, and a whole class of failure modes to solve a
problem this system does not have. If the catalogue grew to where publish took minutes,
the run row already exists to back an async job — the schema wouldn't change.

**Sync SQLAlchemy, not async.** The data volume is tiny and async would add bug surface
for no measurable benefit here.

**JWT in `localStorage`.** Honest trade-off: it's readable by any XSS on the CMS origin.
An httpOnly, SameSite cookie plus CSRF protection is the right answer for production. I
took the simpler route for a take-home and am flagging it rather than hiding it.

**Seeded passwords in env defaults.** Fine for a take-home with throwaway credentials,
and stated as such. A real system would not create accounts from environment variables.

---

## 4. Written answers

The brief asks for at most a page. **These five short answers are that page** — each
links to the longer version below for anyone who wants the reasoning behind it.

1. **[Atomic publish](#41-how-did-you-make-publishing-atomic-and-what-happens-if-the-process-dies-mid-publish).**
   Nothing is ever overwritten. Each run writes a new immutable key,
   `catalogs/{run_id}.json`, and a single committed transaction moves the `is_current`
   pointer — a partial unique index makes "at most one live catalogue" a database
   guarantee. Readers resolve the pointer, then read that key, so they only ever see a
   file some run finished writing. A crash before the write leaves the pointer alone;
   between write and flip leaves an orphan file nothing references; during the write is
   impossible to observe (temp-file + `os.replace` locally, atomic PUT on S3/R2). A
   failed publish always leaves the previous catalogue serving — there is a test that
   forces `storage.put` to raise and asserts exactly that.
2. **[Local disk → R2](#42-your-storage-abstraction-what-changes-to-move-from-local-disk-to-cloudflare-r2).**
   One class and an environment variable, no call-site changes. Everything that persists
   bytes goes through the `Storage` protocol; `get_storage()` picks the implementation
   from `STORAGE_BACKEND`. `R2Storage` is an honest stub whose docstring spells out the
   boto3 calls. The swap is safe because the two semantics the publish job depends on —
   per-object atomic PUT and read-after-write consistency — both hold on R2.
3. **[Search](#43-search-how-is-it-implemented-at-what-size-does-it-stop-working-and-what-next).**
   Server-side, in memory, over the *published catalogue* — not the browser, not the
   database. A flat normalised index cached by catalogue checksum; `q`, `category`,
   `language` and `section` compose with AND. It is a linear scan: fine to roughly
   10⁴–10⁵ entries, and match quality (no typo tolerance, no stemming, no ranking) bites
   before size does. Next: a `published_entries` projection with Postgres `tsvector` +
   GIN written at publish time, then a dedicated engine fed by the same publish job.
4. **[Why a published file](#44-why-serve-a-pre-published-catalogue-file-instead-of-querying-the-database-per-request-where-does-that-bite).**
   It decouples the viewer's read path from admin write load and schema churn, is
   trivially cacheable (the checksum is a perfect ETag), survives a database outage, and
   makes "unpublished content cannot leak" structural rather than a `WHERE` clause you
   have to remember. It bites on staleness, on publishing becoming a human bottleneck,
   on personalisation being impossible from a shared file, on monotonic payload growth,
   and on publish being all-or-nothing.
5. **[Left out, and AI use](#45-what-did-you-leave-out-and-which-ai-tools-did-you-use).**
   Left out: all three stretch goals, E2E tests, video playback, refresh tokens, the
   orphan-file cleanup job, frontend unit tests, a real deploy. Used Claude throughout,
   and the three corrections that mattered were the artwork tolerance rule (its "up to
   2× spec" band would have accepted `banner_too_big.png`, which is exactly 2×), an
   unmaintainable partial-index hack in the models, and a publish-gate deadlock that a
   fully green test suite still missed.

---

### 4.1 How did you make publishing atomic, and what happens if the process dies mid-publish?

Two mechanisms, one for the object and one for the pointer.

The catalogue is **never overwritten**. Each run writes a brand-new key,
`catalogs/{run_id}.json`. The live object is immutable once written. What makes a
catalogue "live" is a single row: the `publish_runs` row with `is_current = true`, which
a partial unique index guarantees at most one of. Publishing ends with one transaction
that clears the old pointer and sets the new one. Readers resolve the pointer, then read
that key — so they only ever see a complete file that some run finished writing.

The sequence is: take a Postgres advisory lock → insert the run row as `running` and
**commit it immediately in its own transaction** → snapshot published content → build
and checksum → if the checksum matches the live one, mark `noop` and stop → write the
new object → flip the pointer in one transaction.

Crash windows, exhaustively:

- **Before the object write.** No file, pointer untouched. The run is left `running` (a
  hard kill) or marked `failed` with the error text (an exception). Readers keep seeing
  the previous catalogue. Committing the run row up front is what makes a crash leave
  evidence instead of silence.
- **Between the write and the pointer flip.** The new file exists but nothing references
  it. It is inert garbage; readers still see the previous catalogue. A cleanup job
  reaping files whose run isn't current is obvious future work — not implemented.
- **During the object write.** Cannot be observed partially. `LocalDiskStorage` writes to
  a temp file in the destination directory, `fsync`s, then `os.replace`s — an atomic
  rename. S3/R2 PUTs are atomic per object. This is why the design ports unchanged.
- **During the pointer flip.** It's one transaction; it commits or it rolls back. There
  is no state with two current catalogues, because the database forbids it.

Any exception marks the run `failed` and leaves the pointer alone, so **a failed publish
always leaves the previous catalogue serving**. There's a test that monkeypatches
`storage.put` to raise, then asserts the pointer never moved and the bytes readers get
are byte-identical to before.

Concurrency: `pg_try_advisory_lock` means a second concurrent publish gets a clean 409
"someone else is publishing right now" rather than interleaving.

### 4.2 Your storage abstraction: what changes to move from local disk to Cloudflare R2?

One class, and an environment variable. No call site changes.

Everything that persists bytes goes through the `Storage` protocol
(`put`/`get`/`exists`/`delete`/`public_url`). `get_storage()` picks the implementation
from `STORAGE_BACKEND`; nothing else in the codebase names a concrete backend.

To switch: implement `R2Storage` with boto3 against R2's S3-compatible endpoint
(`put_object`, `get_object`, `head_object`, `delete_object`), set `STORAGE_BACKEND=r2`
plus the four credential vars, and point `R2_PUBLIC_BASE_URL` at the bucket's public
domain. `backend/app/storage/r2.py` is a stub whose docstring spells out the exact
implementation; it raises `NotImplementedError` rather than pretending to work, because
shipping untested credential-handling code would be worse than shipping an honest stub.

The two semantics the publish job depends on both hold on R2, which is the real reason
the swap is safe: **per-object atomic PUT** (no torn reads) and **read-after-write
consistency** (the pointer flip can safely reference a key written a moment earlier).

One operational change: `/media/{key}` currently proxies bytes through the API. With R2
it becomes a redirect, or goes away entirely in favour of the bucket domain — which also
takes image traffic off the API and puts it on a CDN.

### 4.3 Search: how is it implemented, at what size does it stop working, and what next?

**Server-side, in memory, over the published catalogue** — not in the browser, and not
against the database.

On first request the API builds a flat index over the current catalogue: for each entry,
a normalised haystack of show title + synopsis + episode title + categories (casefolded,
accent-stripped, whitespace-collapsed). `q` substring-matches that; `category`,
`language` and `section` filter alongside it; all four compose with AND. The index is
cached and keyed by catalogue checksum, so it rebuilds only when a publish actually
changes something.

Searching the *published artifact* rather than the database is a correctness argument,
not just a performance one: the viewer must never see unpublished content, and if the
search corpus **is** the published file, that's true by construction rather than by
remembering to add `WHERE status='published'` everywhere.

**Where it stops working.** It's a linear scan. At the current scale (8 shows, ~60
entries) it's microseconds. It stays comfortably under 10 ms to roughly 10⁴–10⁵ entries
and a single-digit-MB catalogue. It breaks on two axes:

1. **Memory/size** — when the catalogue no longer fits comfortably in each API process,
   or when re-parsing it per deploy becomes a real cost.
2. **Match quality**, which bites first in practice. Substring matching has no typo
   tolerance, no stemming, and no ranking — "sing" won't find "songs", and everything
   that matches comes back in catalogue order rather than by relevance.

**Next steps, in order:** a `published_entries` projection table written during the
publish job, with a Postgres `tsvector` + GIN index — this keeps one datastore and gets
stemming and ranking. Beyond that, a dedicated engine (Meilisearch, Typesense,
OpenSearch) fed by the publish job as its indexing pipeline, which is where typo
tolerance and per-locale analysis get good. The publish job is already the natural
indexing hook, so neither step disturbs the read path's shape.

### 4.4 Why serve a pre-published catalogue file instead of querying the database per request? Where does that bite?

**Why.** The viewer's read path is decoupled from admin write load and from schema
churn: editors can be mid-edit on a hundred episodes and the viewer is unaffected,
because it reads a file that changes only when someone deliberately publishes. It's
trivially cacheable — the checksum is a perfect ETag, so revalidation is a 304 and the
file can sit on a CDN. It survives a database outage. It's a reviewable, diffable,
versioned artifact: every run is retained, so "what exactly did we ship on Tuesday" is
answerable. And correctness is structural — unpublished content cannot leak, because it
isn't in the file.

**Where it bites.**

- **Staleness.** Between publishes the viewer is out of date by construction. A typo fix
  isn't live until someone publishes, which makes publishing a human bottleneck and an
  operational ritual.
- **No personalisation.** A shared file is the same for everyone. Per-user continue-
  watching, recommendations or region-specific availability cannot come from it; they'd
  need a separate per-user read path, at which point you have two systems.
- **Monotonic payload growth.** The whole catalogue is one document. It's fine at 8
  shows and painful at 10,000 — eventually it needs sharding by section, pagination, or
  per-show endpoints, and the viewer has to learn to fetch pieces.
- **Publish is all-or-nothing.** There's no partial publish: one blocking problem
  anywhere blocks the whole catalogue. That's defensible for a small catalogue and
  becomes untenable as the content team grows, where per-show publishing would be the
  natural evolution.
- **Write amplification.** Changing one episode title rewrites the entire document.
  Irrelevant now; a real cost at scale.

### 4.5 What did you leave out, and which AI tools did you use?

**Left out, deliberately:**

- **All three stretch goals** (versioned rollback, publish dry-run diff, audit log).
  Worth noting that rollback is ~90% built already: every run's file is retained and the
  live one is chosen by a pointer, so rolling back is `UPDATE publish_runs SET
  is_current` to an older succeeded run. I didn't wire the endpoint or the UI.
- **E2E tests (Playwright).** The time was better spent on the publish job's tests. The
  screen recording demonstrates the flow instead.
- **Video playback.** Out of scope; the viewer is a browse surface.
- **Refresh tokens / token rotation.** 12-hour JWTs, no refresh. A real system needs
  short access tokens plus rotating refresh tokens.
- **A cleanup job for orphaned catalogue files** from crashed publishes. Identified in
  the crash analysis, not built.
- **Frontend unit tests.** The frontends are covered by `tsc --noEmit`, eslint and a real
  production build in CI, which is the honest bar for this scope. I'd add tests for the
  artwork-slot error rendering first.
- **A real deploy.** The CI deploy job is written out and explained but gated `if:
  false` — there's no cloud account behind this repo, and a job that pretends otherwise
  would be dishonest.

**AI tools.** I used Claude throughout — for scaffolding the CRUD routers and the React
pages, for the CSS, and as a reviewer on the publish-job design. Two concrete places I
rejected or corrected its output:

1. **The artwork tolerance rule.** The plan I was working from proposed accepting
   dimensions from "spec minimum up to 2× spec". I measured the fixtures first and found
   `banner_too_big.png` is *exactly* 2× (2560×1440) with a correct 16:9 ratio and only
   13.8 KB — so that rule would have accepted the file whose entire purpose is to be
   rejected, and neither the ratio nor the size check would have caught it either. I
   replaced it with an exact-dimension rule. This is the single most valuable thing that
   came out of measuring the inputs before writing code.

2. **The `(content_group, language)` partial index.** The first cut of the models used a
   SQLAlchemy walrus-expression hack to attach the partial index, then mutated
   `__table__.indexes` afterwards to fix it up. It technically worked and was
   unmaintainable. I rewrote it as a plain `Index(..., postgresql_where=text(...))` in
   `__table_args__`.

The third one was mine, and it is the reason I ran the whole editor loop by hand instead
of trusting a green test suite: `episodes.seed_issue` marks a row that arrived broken,
the publish gate treated it as a blocker, and the gate cleared it only *after* the row
passed — so the flag blocked its own removal and a flagged row could never be published
no matter what an editor fixed. Every test passed, because every test asserted the
refusal. It took walking the loop in a browser-adjacent way — upload the thumbnail the
message asked for, then watch it refuse anyway — to see it. Two smaller versions of the
same mistake turned up alongside it: the report told editors to put a fallback thumbnail
on the show, and the CMS had no show-level thumbnail slot; and it told them to delete a
bad row, and there was no delete button. **An error message naming an action the UI
cannot perform is worse than no message.**

Smaller ones: the generated catalogue checksum initially covered `generated_at`, which
would have made idempotency unachievable — every republish would have looked like a
change. And a first draft of the seed ingest "helpfully" skipped the duplicate row
rather than quarantining it, which would have destroyed the evidence the validation
report exists to surface.

---

## 5. The two frontends

Both are React 18 + TypeScript + Vite + TanStack Query, served as static nginx images
that proxy `/api` and `/media` to the API — so the browser never makes a cross-origin
request and there is no CORS configuration to get wrong.

**CMS (`:3000`) — for someone who does this fifty times a week.**

- **Shows list** — debounced server-side search, filters for section / status / language
  (composed with AND in SQL), server pagination. Filters live in the URL, so a filtered
  view is a shareable link.
- **New show** — title, synopsis, section. Starts as a draft and lands straight in the
  editor, because a show is never finished at the moment it is created.
- **Show editor** — details (title, synopsis, section, categories, featured), the three
  labelled **artwork slots**, and seasons/episodes with inline add, rename, edit and
  delete. Season 0 is labelled "shown as trailers, not a season" so nobody has to
  remember the convention. Episode durations are typed as `mm:ss`. A row imported with a
  problem is highlighted with the problem printed on it — and every action the report
  tells an editor to take is one they can actually take here, including deleting a bad
  row and putting a fallback thumbnail on the show.
- **Artwork slots** state their required dimensions (read from `reference.json`),
  pre-check the file in the browser for instant feedback, then **upload anyway and show
  the server's verdict** — the client check is a convenience, the server is the
  authority, and when they disagree it is the server's message that is displayed.
- **Publish page** — the validation report grouped by show with deep links into the
  editor, run history, and a publish button that is **disabled with the reasons listed
  directly underneath it**. An editor sees the whole page with a permission-denied
  panel: the report exists to tell them what to fix, so hiding it would defeat it.
- **States** — every query renders loading / empty / error-with-retry; mutations disable
  while pending and toast the outcome; a 401 drops the token and returns to login; a
  403 renders inline rather than hiding the page.

**Viewer (`:3001`) — reads the published catalogue and nothing else.**

- **Home** — featured hero using the **banner**, horizontal scroll rows per section
  using **posters**.
- **Show detail** — banner header, synopsis, season tabs, episode list using
  **thumbnails**. Season 0 never appears as a season; trailers get their own row. A
  grouped episode shows its language badges and a toggle that swaps which variant's
  metadata is displayed — the grouping has to be visible in the UI, not just in the JSON.
- **Search** — debounced, server-side, with category and language filters whose options
  come from the catalogue itself, and an empty state that suggests clearing a filter.
- **Slow images** — one `<Artwork>` component used everywhere: the box reserves its
  aspect ratio before the image arrives (no layout shift), images below the fold load
  lazily, and a failed load becomes a titled tile rather than a broken-image icon.

No video playback — the viewer is a browse surface, and playback is out of scope.

---

## 6. API

Error shape is identical everywhere — one exception handler, one envelope:

```json
{ "error": { "code": "wrong_dimensions", "message": "…", "details": { } } }
```

`message` is always written for a person to read, and the CMS renders it verbatim rather
than inventing its own copy. Messages are written once, on the server.

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/auth/login` | — | Returns a 12h JWT |
| GET | `/api/auth/me` | user | |
| GET | `/api/reference` | user | Sections, categories, languages, artwork specs — straight from `reference.json`, so the CMS restates none of them |
| GET | `/api/shows` | user | `?q=&section=&status=&language=&page=&page_size=` — all compose with AND, paginated in SQL |
| POST | `/api/shows` | user | |
| GET | `/api/shows/{id}` | user | Embeds seasons + episodes + artwork (saves the CMS N requests) |
| PATCH | `/api/shows/{id}` | user | `status: published` re-runs the publish gate |
| DELETE | `/api/shows/{id}` | user | Cascades; storage objects deleted best-effort |
| POST | `/api/shows/{id}/seasons` | user | |
| PATCH | `/api/seasons/{id}` | user | |
| DELETE | `/api/seasons/{id}` | user | |
| POST | `/api/seasons/{id}/episodes` | user | |
| PATCH | `/api/episodes/{id}` | user | |
| DELETE | `/api/episodes/{id}` | user | |
| POST | `/api/artwork` | user | multipart; server-side Pillow validation |
| DELETE | `/api/artwork/{id}` | user | |
| GET | `/api/admin/validation-report` | user | Readable by editors — it exists to tell them what to fix |
| POST | `/api/admin/catalog/publish` | **admin** | 403 for editors |
| GET | `/api/admin/publish-runs` | user | |
| GET | `/api/catalog` | **public** | ETag + 304; the viewer's read |
| GET | `/api/catalog/search` | **public** | `?q=&category=&language=&section=` |
| GET | `/api/health` | public | |
| GET | `/media/{key}` | public | |

### Data model and indexes

```
users         id · email UNIQUE · password_hash · role(editor|admin)
shows         id · slug UNIQUE · title · synopsis · section(NULL-able) · categories[]
              · status · featured · timestamps
seasons       id · show_id→shows · number · title      UNIQUE(show_id, number)
episodes      id · season_id→seasons · external_id · number · title · synopsis
              · duration_seconds(NULL-able) · language · content_group · status
              · seed_issue · timestamps
artwork       id · owner_type(show|episode) · owner_id · kind(poster|banner|thumbnail)
              · storage_key · width · height · size_bytes · content_type · checksum
              UNIQUE(owner_type, owner_id, kind)
publish_runs  id · started_at · finished_at · actor_id→users · status · counts(jsonb)
              · catalog_key · checksum · error · is_current
```

Every index earns its place:

- `ix_shows_status_section` — serves the publish snapshot (`WHERE status='published'`)
  and the CMS section filter, the only two bulk scans of this table.
- `uq_episodes_content_group_language` — **partial** unique on
  `(content_group, language) WHERE content_group IS NOT NULL`. Enforces the challenge's
  hard rule while letting many rows share a NULL content_group.
- `ix_episodes_season_number` — every episode read is "the episodes of this season, in
  order".
- `uq_publish_runs_single_current` — **partial** unique on `is_current WHERE is_current`.
  Makes "at most one live catalogue" a database guarantee rather than a convention, and
  doubles as the pointer lookup for `GET /catalog`.
- `ix_seasons_show_id`, `ix_episodes_external_id`, plus PK/FK indexes.

No full-text index: search runs over the published catalogue, not the database (§4.3).

Migrations are a single hand-written initial revision. It's hand-written rather than
autogenerated because Alembic's autogenerate doesn't reliably round-trip
`postgresql_where`, and both partial indexes matter.

---

## 7. Pipeline and operability

### CI

`.github/workflows/ci.yml` runs four jobs:

- **backend** — `ruff check`, `ruff format --check`, `alembic upgrade head` against a
  real Postgres service container (so migrations are proven to apply, not just to
  parse), then `pytest`.
- **frontend** — matrix over both apps: `npm ci` (the committed lockfile is the input,
  so CI tests the tree that ships), `typecheck`, `lint`, `build`.
- **images** — `docker compose build`, proving every Dockerfile builds.
- **deploy** — written out in full, gated `if: false`. It logs into GHCR and pushes the
  three images; the steps after that are commented because they're platform-specific.

### How I'd deploy this

Images go to a registry (GHCR above). On the target — Fly.io, ECS, or a plain Docker
host — the shape is the same:

1. **Migrations as a release step**, before any new container takes traffic. Migrations
   are written to be backward-compatible with the currently-running version so old and
   new code can overlap during a rolling deploy.
2. **Roll the API**, gating each new instance on `/api/health` — which verifies the
   database and a real storage write, not just that the process answers — before the
   load balancer sends it traffic.
3. **Roll the two static frontends.** They're stateless nginx images.
4. **Keep the previous image tagged**, so rollback is a tag flip rather than a rebuild.

### Secrets

`.env.example` documents every variable. In development they live in a gitignored
`.env`; in CI they're GitHub Actions secrets; in production they come from the
platform's secret manager (Fly secrets, AWS Secrets Manager, etc.) and are injected as
environment variables at run time.

The principle is that **secrets live in the runtime, not in the artifact** — never baked
into an image, never committed. R2 credentials should be scoped per-bucket with only the
needed operations; `JWT_SECRET` should be rotated (rotation forces re-login, which is
why short-lived tokens plus refresh tokens matter in a real deployment); `DATABASE_URL`
is injected by the platform.

The values shipped in `docker-compose.yml` are deliberately weak development defaults so
the stack runs with zero configuration. They are not secrets and must not survive
contact with a real environment.

### Health and the one thing I'd alert on

`GET /api/health` checks dependencies rather than liveness: it runs `SELECT 1`, performs
a real write-read-delete probe against storage (a read-only mount is a genuine failure
mode that an existence check misses), and reports the current catalogue's run id and
age. It returns 503 when degraded, so a load balancer will pull the instance.

**The one alert: the failure rate and availability of `GET /api/catalog`.**

That endpoint is the entire child-facing product. If it fails, every viewer session is
broken — nobody can browse anything. Every other failure in this system degrades to
"the content team is inconvenienced": if publishing breaks, the last good catalogue
keeps serving and children notice nothing; if the CMS is down, editors are blocked but
viewers are fine. Only `/catalog` failing is visible to the people the product is for.
I'd page on it, alerting on error rate and p99 latency over a short window.

Honourable mention, at a lower severity: **`publish_runs.status = 'failed'`**, or a
current catalogue whose age exceeds the team's normal publishing cadence. Neither breaks
viewing, so neither should wake anyone up — but both are silent by nature. A failed
publish looks exactly like "nobody published today", and the content team can spend a
day wondering why their changes aren't live. That's a ticket, not a page.

---

## 8. Verification performed

- `ruff check` and `ruff format --check` clean across the backend.
- Both frontends build under `tsc` strict mode with `noUnusedLocals`.
- **The viewer/admin boundary is verified mechanically**, not just by inspection:

  ```bash
  grep -rniE "admin|token|login|password|authorization|bearer" viewer/src
  ```

  returns zero hits. The viewer's entire network surface is `viewer/src/api.ts`, which
  contains exactly two URLs: `/api/catalog` and `/api/catalog/search`.
- The artwork validator is tested against all six supplied fixtures, asserting the
  specific error code for each rejection.

### The full stack was verified end to end in Docker

Not just unit-tested — the actual `docker compose up` a reviewer will run, from an empty
volume, with no `.env` file present:

| Check | Result |
|---|---|
| `docker compose up --build` from zero (no `.env`, `down -v` first) | db, api, cms and viewer all answering 200 in **17–18s** with a warm build cache; api reports `healthy`. A reviewer's *first* build has to pull base images and run `pip install` / `npm ci`, which is a few minutes of one-time work before that 17s. |
| Migrations on an empty database | `Running upgrade -> 0001, initial schema`, clean |
| Seed ingest | 8 shows, 10 seasons, **95 episodes**, 2 findings logged |
| Planted defect — `ep_0036` missing artwork | quarantined `draft`, blocks publish |
| Planted defect — `ep_9001` duplicate `content_group` | quarantined `draft`, blocks publish |
| Planted defect — `rhyme-rangers` missing section | stays `draft`; listed in the report as 2 **warnings**; `— none —` in the CMS list |
| Initial autopublish | 7 shows, 65 entries, 4 sections in `reference.json` order |
| `GET /api/catalog` | 200; `If-None-Match` → **304** |
| Artwork upload, all 6 fixtures into the **poster** slot | only `poster_good.jpg` accepted (201); the other five 422 |
| Artwork upload, each fixture into its **matching** slot | 3 good accepted (201); `poster_wrong_ratio` → `wrong_aspect_ratio`, `banner_too_big` → `wrong_dimensions`, `thumb_tiny` → `wrong_dimensions` |
| Uploaded file served back | 200 `image/jpeg`, via the API **and** through the nginx proxy |
| Role enforcement | editor publish → **403**; anonymous CRUD → **401**; anonymous `/api/catalog` → **200**; auth enforced through the proxy too |
| Publish gate | refused with the 3 blockers listed |
| **The whole editor loop** | blocked → upload wrong asset (422, readable) → upload right asset (201) → episode publishes → delete the bad re-upload → report clears → **publish succeeds** (7 shows, 66 entries) → **republish unchanged → `noop`** → the fixed episode is in `GET /api/catalog` |
| Create flows | show → season → rename season → episode, all via the API the CMS uses; duplicate `(content_group, language)` → **409** |
| `pytest` inside the container | **70 passed** |
| Viewer and CMS in a real browser | home, show detail (trailers row + EN/HI toggles), search with filters, CMS list, show editor and publish page all render, **zero console errors** |

One quirk worth naming, since it looks inconsistent at first glance: **the startup
autopublish bypasses the validation gate that the admin endpoint enforces.** Seeding
calls `publish()` directly, so `docker compose up` always yields a populated viewer;
pressing *Publish catalogue* in the CMS runs `collect_issues()` first and refuses while
the three seeded blockers stand (that gap is exactly the demo: 65 entries at boot, 66
once an editor resolves them). That is deliberate — a reviewer should see a working
catalogue immediately, but a human-initiated publish should not quietly ship content an
editor has not resolved. The bootstrap publishes only the valid subset either way.

---

## 9. Time spent

| Phase | Time |
|---|---|
| Acquire + audit seed data (incl. recovering the assets from the PDF's link annotations) | 15 min |
| Environment setup | see note below |
| Schema, models, migrations | 40 min |
| Storage abstraction + artwork validator + its tests | 45 min |
| Catalogue builder + its 18 tests | 50 min |
| Publish job, search, validation report | 55 min |
| CRUD API, auth, role enforcement | 50 min |
| CMS | 60 min |
| Viewer | 45 min |
| CI, compose, `.env.example` | 25 min |
| README + written answers | 30 min |
| Docker verification + fixes it surfaced | 50 min |
| Final pass: re-read the brief line by line against the built system, fixed the gaps it found (D-012 to D-015) | 60 min |

**The note on environment setup.** The machine I built this on had no Docker and no WSL,
and I could not assume I would get them. Rather than develop against SQLite and hope the
Postgres-specific parts held, I installed a Postgres 16 instance locally without admin
rights and ran everything against that from the start. Docker came later, and the
container run reproduced the local results exactly.

That ordering paid for itself. A real Postgres caught three things SQLite would have
waved through — a duplicate `CREATE TYPE` in the initial migration, a validation
heuristic firing fifteen times instead of once, and a `passlib`/`bcrypt` version clash —
and containerising afterwards caught a fourth: the README's documented test command was
both broken and destructive, because it pointed a suite that runs `DROP SCHEMA public
CASCADE` at the live database. That is now a profile-gated `test` service on its own
database.
