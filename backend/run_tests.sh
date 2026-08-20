#!/bin/sh
# Runs the backend suite inside the container.
#
# The tests rebuild the schema with `DROP SCHEMA public CASCADE`, so they must never
# point at the `peblo` database the running stack is serving from. This script targets
# a separate `peblo_test` database and creates it on first use.
#
# Dev dependencies are installed here rather than baked into the image, so the shipped
# API image stays production-only.
set -e

pip install --no-cache-dir -q -r requirements-dev.txt

python - <<'PY'
import os
import psycopg

dsn = os.environ["ADMIN_DATABASE_URL"]
name = os.environ.get("TEST_DB_NAME", "peblo_test")
with psycopg.connect(dsn, autocommit=True) as conn:
    exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
    if not exists:
        conn.execute(f'CREATE DATABASE "{name}"')
        print(f"created {name}")
PY

exec pytest -q "$@"
