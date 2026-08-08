#!/usr/bin/env bash
#
# Container entrypoint.
#
# Waits for dependencies, then runs migrations and permission seeding exactly
# once per deploy before handing over to the process in CMD.
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
            log "ERROR: ${name} did not become reachable in 60 attempts."
            exit 1
        fi
        sleep 2
    done
    log "${name} is up."
}

wait_for "${POSTGRES_HOST:-postgres}" "${POSTGRES_PORT:-5432}" "postgres"

REDIS_HOST="$(python -c "
from urllib.parse import urlparse
import os
print(urlparse(os.environ.get('REDIS_URL', 'redis://redis:6379/0')).hostname or 'redis')
")"
REDIS_PORT="$(python -c "
from urllib.parse import urlparse
import os
print(urlparse(os.environ.get('REDIS_URL', 'redis://redis:6379/0')).port or 6379)
")"
wait_for "${REDIS_HOST}" "${REDIS_PORT}" "redis"

# Only the web role migrates. Workers starting concurrently would otherwise
# race on the same migration and one would fail.
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    log "applying migrations…"
    python manage.py migrate --noinput

    log "seeding permissions and roles…"
    python manage.py seed_permissions
fi

log "starting: $*"
exec "$@"
