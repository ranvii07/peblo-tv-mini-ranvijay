"""Test fixtures.

Integration tests need a real Postgres, because the schema leans on Postgres-specific
features that SQLite cannot emulate: partial unique indexes, array columns and advisory
locks. Testing them against SQLite would prove nothing about what actually runs.

If DATABASE_URL is not reachable, the integration tests skip rather than fail, so the
pure-function suites (catalogue builder, artwork validator) still run anywhere.
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

    from app.core.config import get_settings

    try:
        engine = create_engine(get_settings().database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture()
def app_env(tmp_path, db_available, monkeypatch):
    """A migrated, empty database plus an isolated storage root."""
    if not db_available:
        pytest.skip("no database reachable — integration test skipped")

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
