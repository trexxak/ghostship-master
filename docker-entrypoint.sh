#!/usr/bin/env bash
set -euo pipefail

# Load environment variables from .env if present.  Iterate over each
# non‑comment line and export it into the environment.  This approach
# avoids mangling values containing whitespace, commas or other
# special characters.  Existing environment variables take
# precedence over .env definitions.
if [ -f /app/.env ]; then
  while IFS='=' read -r key value; do
    # Skip empty lines and comments
    if [[ -z "${key}" || "${key}" == \#* ]]; then
      continue
    fi
    # Only set the variable if it is not already defined
    if [ -z "${!key:-}" ]; then
      export "${key}"="${value}"
    fi
  done < /app/.env || true
fi

# Run database migrations on startup if requested.  Worker-style services can
# opt out (RUN_MIGRATIONS_ON_START=0) and instead wait for another container
# to finish applying migrations (WAIT_FOR_MIGRATIONS=1).

echo "Running Django migrations..."
/usr/local/bin/python manage.py migrate --noinput || true

RUN_MIGRATIONS_ON_START="${RUN_MIGRATIONS_ON_START:-1}"
WAIT_FOR_MIGRATIONS="${WAIT_FOR_MIGRATIONS:-0}"

if [[ "${RUN_MIGRATIONS_ON_START}" != "0" ]]; then
  echo "== Entrypoint: applying migrations =="
  python forum_simulator/manage.py migrate --noinput || true
elif [[ "${WAIT_FOR_MIGRATIONS}" != "0" ]]; then
  echo "== Entrypoint: waiting for migrations to complete =="
  until python forum_simulator/manage.py migrate --check --noinput >/dev/null 2>&1; do
    echo "   migrations not ready, retrying..."
    sleep 2
  done
  echo "== Entrypoint: migrations detected, continuing =="
fi

# Hand over control to whatever command was provided.  When started via
# Docker Compose, this will likely be the development bootstrap
# script; otherwise it might be a shell or other management command.
exec "$@"
