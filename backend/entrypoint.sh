#!/bin/sh
# Ordered startup: migrate, seed, serve. Any failure before uvicorn aborts the
# container rather than serving a half-configured API.
set -e

echo "==> running migrations"
alembic upgrade head

echo "==> seeding (idempotent)"
python -m app.seed_cli

echo "==> starting api"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
