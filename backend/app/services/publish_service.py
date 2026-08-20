"""The publish job.

The contract, in one sentence: **a reader never sees a half-written catalogue, and
never sees a catalogue that no run points at.**

How that is achieved:

1. Take a Postgres advisory lock, so two concurrent publishes cannot interleave. The
   second caller is told to try again rather than being silently queued.
2. Insert the `publish_runs` row as `running` and **commit it immediately**, in its own
   transaction. If the process dies at any later point, that row survives as evidence
   that a publish was attempted — a crash leaves a trace, not silence.
3. Read a consistent snapshot of published content in one transaction.
4. Build the catalogue (pure function) and checksum its content.
5. If the checksum matches the live catalogue, mark the run `noop` and stop. Nothing is
   written; republishing unchanged content genuinely changes nothing.
6. Write the catalogue to a **new key** — `catalogs/{run_id}.json`. The live object is
   never touched, never overwritten, never mutated.
7. In a single transaction, clear the old `is_current` pointer and set this run to
   `succeeded, is_current=true`. This is the moment the change becomes visible, and it
   is atomic because it is one transaction against one row apiece.

Crash windows, exhaustively:

* **Before step 6** — no file written, pointer untouched. Run is left `running` (hard
  kill) or marked `failed` (exception). Readers keep seeing the previous catalogue.
* **Between 6 and 7** — the new file exists but nothing points at it. It is inert
  garbage; readers still see the previous catalogue. A future cleanup job could reap
  files belonging to non-current runs; not implemented, noted in the README.
* **During step 6's write** — impossible to observe partially: local disk writes to a
  temp file and renames, and S3/R2 PUTs are atomic per object.
* **During step 7** — the transaction either commits or rolls back. There is no state
  where two runs are current: a partial unique index on `is_current` forbids it.

The pointer is the only thing that decides what readers see, and flipping it is a single
committed transaction. That is the whole atomicity argument.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.core.config import Reference
from app.models import (
    Artwork,
    ArtworkKind,
    OwnerType,
    PublishRun,
    PublishStatus,
    Season,
    Show,
    Status,
)
from app.services.catalog_builder import build_catalog, canonical_bytes, catalog_checksum
from app.storage import Storage

# Arbitrary but fixed application-level lock id for the publish critical section.
PUBLISH_LOCK_ID = 0x9EB10


class PublishInProgress(Exception):
    """Another publish holds the lock right now."""


def _artwork_maps(db: Session, storage: Storage):
    show_art: dict[int, dict[str, str]] = {}
    episode_art: dict[int, dict[str, str]] = {}
    for art in db.scalars(select(Artwork)):
        url = storage.public_url(art.storage_key)
        target = show_art if art.owner_type is OwnerType.show else episode_art
        target.setdefault(art.owner_id, {})[art.kind.value] = url
    return show_art, episode_art


def build_snapshot(db: Session, storage: Storage) -> dict:
    """Read every published show/episode into the plain-dict shape build_catalog wants.

    Filtering to published happens here, in the query, so the builder stays pure and the
    "only published content appears" rule is enforced by the database rather than by
    remembering to check it in three places.
    """
    show_art, episode_art = _artwork_maps(db, storage)

    shows = db.scalars(
        select(Show)
        .where(Show.status == Status.published, Show.section.isnot(None))
        .options(selectinload(Show.seasons).selectinload(Season.episodes))
        .order_by(Show.title, Show.id)
    ).all()

    out_shows = []
    for show in shows:
        seasons = []
        for season in show.seasons:
            episodes = []
            for ep in season.episodes:
                if ep.status is not Status.published:
                    continue
                # Defence in depth: content that cannot legally be published must not
                # reach the catalogue even if a status was set by some other path.
                if not ep.duration_seconds or ep.duration_seconds <= 0:
                    continue
                thumb = episode_art.get(ep.id, {}).get(ArtworkKind.thumbnail.value) or show_art.get(
                    show.id, {}
                ).get(ArtworkKind.thumbnail.value)
                if not thumb:
                    continue
                episodes.append(
                    {
                        "id": ep.id,
                        "number": ep.number,
                        "title": ep.title,
                        "synopsis": ep.synopsis,
                        "duration_seconds": ep.duration_seconds,
                        "language": ep.language,
                        "content_group": ep.content_group,
                        "thumbnail": thumb,
                    }
                )
            if episodes:
                seasons.append(
                    {
                        "number": season.number,
                        "title": season.title,
                        "episodes": episodes,
                    }
                )
        if seasons:
            out_shows.append(
                {
                    "id": show.id,
                    "slug": show.slug,
                    "title": show.title,
                    "synopsis": show.synopsis,
                    "section": show.section,
                    "categories": list(show.categories or []),
                    "featured": show.featured,
                    "artwork": show_art.get(show.id, {}),
                    "seasons": seasons,
                }
            )

    return {"shows": out_shows}


def current_run(db: Session) -> PublishRun | None:
    return db.scalar(select(PublishRun).where(PublishRun.is_current.is_(True)))


def publish(db: Session, reference: Reference, storage: Storage, actor_id: int | None) -> dict:
    """Run a publish. Returns a summary dict; raises PublishInProgress if locked."""
    # ---- 1. serialize concurrent publishes ----------------------------------------
    # The lock is held on a connection of its own for the whole job. A session-level
    # advisory lock belongs to the connection that took it, and this Session hands its
    # connection back to the pool on every commit — of which there are three below.
    # Taking the lock through the Session could therefore release it against a
    # *different* connection at the end and leave the real lock held indefinitely, which
    # would turn every later publish into a permanent 409.
    lock_conn = db.get_bind().connect()
    try:
        got_lock = lock_conn.exec_driver_sql(
            "SELECT pg_try_advisory_lock(%s)", (PUBLISH_LOCK_ID,)
        ).scalar()
    except BaseException:
        lock_conn.close()
        raise
    if not got_lock:
        lock_conn.close()
        raise PublishInProgress()

    run: PublishRun | None = None
    try:
        # ---- 2. record the attempt, committed on its own so a crash leaves evidence
        run = PublishRun(status=PublishStatus.running, actor_id=actor_id)
        db.add(run)
        db.commit()
        db.refresh(run)

        try:
            # ---- 3/4. snapshot and build
            snapshot = build_snapshot(db, storage)
            generated_at = datetime.now(UTC).isoformat()
            catalog = build_catalog(snapshot, reference, run_id=run.id, generated_at=generated_at)
            checksum = catalog_checksum(catalog)

            # ---- 5. idempotency
            live = current_run(db)
            if live is not None and live.checksum == checksum:
                run.status = PublishStatus.noop
                run.finished_at = datetime.now(UTC)
                run.counts = catalog["counts"]
                run.checksum = checksum
                db.commit()
                return {
                    "status": "noop",
                    "run_id": run.id,
                    "message": "The catalogue is already up to date — nothing changed.",
                    "counts": catalog["counts"],
                    "checksum": checksum,
                }

            # ---- 6. write to a NEW key; the live object is never touched
            key = f"catalogs/{run.id}.json"
            storage.put(key, canonical_bytes(catalog), "application/json")

            # ---- 7. flip the pointer in one transaction
            db.execute(
                update(PublishRun).where(PublishRun.is_current.is_(True)).values(is_current=False)
            )
            run.status = PublishStatus.succeeded
            run.finished_at = datetime.now(UTC)
            run.counts = catalog["counts"]
            run.catalog_key = key
            run.checksum = checksum
            run.is_current = True
            db.commit()

            return {
                "status": "succeeded",
                "run_id": run.id,
                "message": (
                    f"Published {catalog['counts']['shows']} shows and "
                    f"{catalog['counts']['entries']} episodes."
                ),
                "counts": catalog["counts"],
                "checksum": checksum,
                "catalog_key": key,
            }

        except Exception as exc:
            # ---- 8. failure leaves the previous catalogue serving, untouched
            db.rollback()
            if run is not None:
                db.execute(
                    update(PublishRun)
                    .where(PublishRun.id == run.id)
                    .values(
                        status=PublishStatus.failed,
                        finished_at=datetime.now(UTC),
                        error=f"{type(exc).__name__}: {exc}"[:2000],
                    )
                )
                db.commit()
            raise
    finally:
        try:
            lock_conn.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (PUBLISH_LOCK_ID,))
        finally:
            lock_conn.close()


def load_current_catalog(db: Session, storage: Storage) -> tuple[dict, str] | None:
    """Return (catalog, checksum) for the live catalogue, or None if nothing published."""
    run = current_run(db)
    if run is None or not run.catalog_key:
        return None
    raw = storage.get(run.catalog_key)
    return json.loads(raw.decode("utf-8")), (run.checksum or "")
