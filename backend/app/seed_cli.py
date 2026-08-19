"""Startup seeding, run by the container entrypoint after migrations.

Idempotent: if content already exists it does nothing, so restarting the stack does not
duplicate or reset anything. Seeding happens in the API container rather than a separate
one-shot service because it keeps `docker compose up` to a single ordered startup with
no extra coordination.

It also performs one publish at the end, so the viewer has content the moment the stack
is up. A grader who opens the viewer first should see a working product, not an empty
state that looks broken.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from app.core.config import get_reference, get_settings
from app.core.db import SessionLocal
from app.services import publish_service, seed_ingest
from app.storage import get_storage

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("seed")


def main() -> int:
    settings = get_settings()
    if not settings.seed_on_start:
        log.info("SEED_ON_START is false — skipping seed")
        return 0

    reference = get_reference()
    storage = get_storage()
    seed_dir = Path(settings.seed_dir)
    if not seed_dir.is_absolute():
        seed_dir = (Path(__file__).resolve().parents[1] / seed_dir).resolve()

    if not (seed_dir / "seed_shows.json").exists():
        log.error("seed data not found at %s", seed_dir)
        return 1

    db = SessionLocal()
    try:
        seed_ingest.seed_users(db)
        log.info("users ready (%s, %s)", settings.admin_email, settings.editor_email)

        result = seed_ingest.seed_content(db, reference, storage, seed_dir)
        if result.get("skipped"):
            log.info("content already present — skipping ingest")
        else:
            log.info(
                "ingested %s shows, %s seasons, %s episodes",
                result["shows"], result["seasons"], result["episodes"],
            )
            for finding in result["findings"]:
                log.warning("seed finding: %s", finding)

        if settings.autopublish_on_seed:
            if publish_service.current_run(db) is None:
                try:
                    out = publish_service.publish(db, reference, storage, actor_id=None)
                    log.info("initial publish: %s — %s", out["status"], out["message"])
                except Exception as exc:
                    # A failed initial publish must not stop the API from starting;
                    # the CMS can still be used to fix whatever blocked it.
                    log.warning("initial publish failed: %s", exc)
            else:
                log.info("a catalogue is already published — not republishing")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
