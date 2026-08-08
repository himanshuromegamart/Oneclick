#!/usr/bin/env bash
#
# Container entrypoint.
#
# Runs in two very different places, and has to suit both:
#
#   docker compose  - Postgres and Redis are sibling containers that may still
#                     be starting, so we wait for them.
#   Render / cloud  - the database is managed (Neon) and reached through
#                     DATABASE_URL; there is nothing to wait for, and waiting
#                     for a host named "postgres" would hang until the platform
#                     gave up and killed the container.
#
set -euo pipefail

log() { echo "[entrypoint] $*"; }

wait_for() {
    local host="$1" port="$2" name="$3" attempts=0
    log "waiting for ${name} at ${host}:${port}…"
    until python -c "
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect(('${host}', ${port}))
except OSError:
    sys.exit(1)
" 2>/dev/null; do
        attempts=$((attempts + 1))
        if [ "${attempts}" -ge 60 ]; then
            log "ERROR: ${name} did not become reachable after 60 attempts."
            exit 1
        fi
        sleep 2
    done
    log "${name} is up."
}

# -- database ---------------------------------------------------------------
# DATABASE_URL means a managed provider: already running, reachable, and quite
# possibly not resolvable by a bare hostname. Django will report a clear error
# on the first query if it is genuinely unreachable.
if [ -n "${DATABASE_URL:-}" ]; then
    log "DATABASE_URL is set - using the managed database, no wait needed."
elif [ -n "${POSTGRES_HOST:-}" ]; then
    wait_for "${POSTGRES_HOST}" "${POSTGRES_PORT:-5432}" "postgres"
else
    log "ERROR: set DATABASE_URL (managed) or POSTGRES_HOST (self-hosted)."
    exit 1
fi

# -- cache ------------------------------------------------------------------
# Redis is optional. Without it the app falls back to an in-process cache, so
# an unset REDIS_URL is a valid configuration rather than a misconfiguration.
if [ -n "${REDIS_URL:-}" ]; then
    REDIS_HOST="$(python -c "
import os
from urllib.parse import urlparse
print(urlparse(os.environ['REDIS_URL']).hostname or '')
")"
    REDIS_PORT="$(python -c "
import os
from urllib.parse import urlparse
print(urlparse(os.environ['REDIS_URL']).port or 6379)
")"
    # Only wait for a Redis we could plausibly be starting alongside. A managed
    # Redis on another network is already up, and waiting on it would be the
    # same trap as waiting on a managed Postgres.
    if [ -n "${REDIS_HOST}" ] && [ "${WAIT_FOR_REDIS:-auto}" != "false" ]; then
        case "${REDIS_HOST}" in
            redis | localhost | 127.0.0.1)
                wait_for "${REDIS_HOST}" "${REDIS_PORT}" "redis" ;;
            *)
                log "REDIS_URL points at ${REDIS_HOST} - assuming it is managed." ;;
        esac
    fi
else
    log "No REDIS_URL - using the in-process cache fallback."
fi

# -- schema -----------------------------------------------------------------
# Only the web role migrates. Workers starting at the same moment would
# otherwise race on the same migration and one of them would fail.
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    log "applying migrations…"
    python manage.py migrate --noinput
fi

log "starting: $*"
exec "$@"
