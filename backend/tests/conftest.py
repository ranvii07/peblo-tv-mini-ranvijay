"""Test fixtures.

Integration tests need a real Postgres, because the schema leans on Postgres-specific
features that SQLite cannot emulate: partial unique indexes, array columns and advisory
locks. Testing them against SQLite would prove nothing about what actually runs.

If DATABASE_URL is not reachable, the integration tests skip rather than fail, so the
pure-function suites (catalogue builder, artwork validator) still run anywhere.

**These tests are destructive.** `app_env` runs `DROP SCHEMA public CASCADE` to rebuild
the schema for each test, so pointing them at a database holding anything you care about
— in particular the `peblo` database the running stack serves from — destroys it. The
guard in `db_available` refuses to run unless the target database name ends in `_test`.
"Remember to set the right DATABASE_URL" is not a safety mechanism; a check is.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "seed"

os.environ.setdefault("SEED_DIR", str(DATA_DIR))
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("STORAGE_BACKEND", "local")


@pytest.fixture(scope="session")
def reference():
    from app.core.config import Reference

    return Reference(json.loads((DATA_DIR / "reference.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="session")
def db_available() -> bool:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from app.core.config import get_settings

    url = make_url(get_settings().database_url)
    if not (url.database or "").endswith("_test"):
        # Not an error — the DB-free suites still run. The integration tests simply
        # refuse to touch a database that is not obviously a throwaway.
        return False

    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture()
def app_env(tmp_path, db_available, monkeypatch):
    """A migrated, empty database plus an isolated storage root."""
    if not db_available:
        pytest.skip(
            "No usable test database. DATABASE_URL must be reachable AND name a database "
            "ending in '_test' — this suite runs DROP SCHEMA public CASCADE, so it will "
            "not point at the live 'peblo' database. Easiest route: "
            "docker compose --profile test run --rm --build test"
        )

    from sqlalchemy import text

    from app.core.config import get_reference, get_settings
    from app.core.db import SessionLocal, engine
    from app.models import Base
    from app.storage.local import LocalDiskStorage

    # Rebuild the schema from the models for each test. Migrations are exercised
    # separately (and in CI) by `alembic upgrade head`.
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)

    storage = LocalDiskStorage(str(tmp_path / "storage"))
    db = SessionLocal()
    try:
        yield {
            "db": db,
            "storage": storage,
            "reference": get_reference(),
            "settings": get_settings(),
            "seed_dir": DATA_DIR,
        }
    finally:
        db.close()


@pytest.fixture()
def seeded(app_env):
    """The real seed data, loaded exactly as the container seeds it."""
    from app.services import seed_ingest

    seed_ingest.seed_users(app_env["db"])
    result = seed_ingest.seed_content(
        app_env["db"], app_env["reference"], app_env["storage"], app_env["seed_dir"]
    )
    return {**app_env, "ingest": result}


@pytest.fixture()
def client(app_env):
    """A TestClient wired to the same session and storage the fixtures use."""
    from fastapi.testclient import TestClient

    from app.core.db import get_db
    from app.main import app
    from app.storage import get_storage

    app.dependency_overrides[get_db] = lambda: app_env["db"]
    app.dependency_overrides[get_storage] = lambda: app_env["storage"]
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
