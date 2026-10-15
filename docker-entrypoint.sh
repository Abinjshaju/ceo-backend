#!/bin/sh
# Fail fast with a readable message when the production database is not configured.
# Without DATABASE_URL the app falls back to a SQLite file under /app, which is read-only.
set -eu
if [ -z "${DATABASE_URL:-}" ]; then
    echo "DATABASE_URL is not set. Set it in the service environment (Postgres URL) and redeploy." >&2
    exit 1
fi
exec "$@"
