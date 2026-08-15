#!/usr/bin/env bash
#
# Start / stop the local Postgres the engine's own database lives in.
#
# There is a real Postgres here rather than SQLite because almost everything
# store.py leans on is Postgres-specific: jsonb with a per-connection codec,
# ON CONFLICT upserts, FILTER aggregates, array_agg, partial indexes and
# gen_random_uuid(). A test that passed against SQLite would prove nothing
# about the queries that actually ship.
#
# Port 55432, not 5432: a Homebrew postgresql@14 is often already listening on
# the default port, and quietly writing the engine's schema into the wrong
# cluster is a much worse failure than a refused connection.
#
# Usage: sql/dev-db.sh {start|stop|restart|status|apply|psql|url|reset}

set -euo pipefail

CONTAINER=${VIRA_PG_CONTAINER:-vira-pg}
VOLUME=${VIRA_PG_VOLUME:-vira-pg-data}
PORT=${VIRA_PG_PORT:-55432}
PG_USER=${VIRA_PG_USER:-vira}
PG_DB=${VIRA_PG_DB:-vira}
IMAGE=${VIRA_PG_IMAGE:-postgres:16}

# Local development only, and the container is bound to 127.0.0.1 so this
# never leaves the laptop. Every deployed environment supplies its own via
# API_DATABASE_URL; nothing reads this default but a dev box.
PG_PASSWORD=${VIRA_PG_PASSWORD:-vira-local-dev}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# podman is command-line compatible with docker for everything used here, so
# whichever one is installed drives the container.
if command -v docker >/dev/null 2>&1; then
    RUNTIME=docker
    IMAGE_REF="$IMAGE"
elif command -v podman >/dev/null 2>&1; then
    RUNTIME=podman
    # podman refuses to guess a registry for a short name, and prompting would
    # hang a non-interactive caller.
    IMAGE_REF="docker.io/library/$IMAGE"
else
    echo "neither docker nor podman is on PATH — see sql/README.md for the Homebrew fallback" >&2
    exit 1
fi

url() { echo "postgresql://${PG_USER}:${PG_PASSWORD}@127.0.0.1:${PORT}/${PG_DB}"; }

exists() { "$RUNTIME" container inspect "$CONTAINER" >/dev/null 2>&1; }

running() { [ "$("$RUNTIME" container inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" = "true" ]; }

wait_ready() {
    for _ in $(seq 1 60); do
        if "$RUNTIME" exec "$CONTAINER" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "postgres did not become ready within 60s" >&2
    return 1
}

start() {
    if [ "$RUNTIME" = podman ] && ! podman info >/dev/null 2>&1; then
        echo "podman VM is not running — start it with: podman machine start" >&2
        exit 1
    fi

    if running; then
        echo "already running on 127.0.0.1:${PORT}"
    elif exists; then
        "$RUNTIME" start "$CONTAINER" >/dev/null
        echo "restarted existing container"
    else
        # The named volume is what makes this a database and not a scratch pad:
        # `stop` keeps the data, only `reset` throws it away.
        "$RUNTIME" run -d \
            --name "$CONTAINER" \
            -e POSTGRES_USER="$PG_USER" \
            -e POSTGRES_PASSWORD="$PG_PASSWORD" \
            -e POSTGRES_DB="$PG_DB" \
            -p "127.0.0.1:${PORT}:5432" \
            -v "${VOLUME}:/var/lib/postgresql/data" \
            "$IMAGE_REF" >/dev/null
        echo "created container ${CONTAINER} from ${IMAGE_REF}"
    fi
    wait_ready
    apply
    echo "API_DATABASE_URL=$(url)"
}

apply() {
    running || { echo "container is not running — sql/dev-db.sh start" >&2; exit 1; }
    # ON_ERROR_STOP so a broken statement fails the script instead of scrolling
    # past. schema.sql is idempotent, so re-applying on every start is the
    # point: it is exactly what the deploy does — which also means every
    # re-apply emits an "already exists, skipping" notice per object, and those
    # are the expected case rather than something worth reading.
    "$RUNTIME" exec -i -e PGOPTIONS="-c client_min_messages=warning" "$CONTAINER" \
        psql -v ON_ERROR_STOP=1 -q -U "$PG_USER" -d "$PG_DB" < "${ROOT}/sql/schema.sql"
    echo "schema applied"
}

case "${1:-start}" in
    start)   start ;;
    stop)    exists && "$RUNTIME" stop "$CONTAINER" >/dev/null && echo "stopped (data kept in volume ${VOLUME})" ;;
    restart) "$0" stop || true; "$0" start ;;
    status)
        if running; then echo "running on 127.0.0.1:${PORT}"
        elif exists; then echo "stopped"
        else echo "not created"; fi ;;
    apply)   apply ;;
    psql)    "$RUNTIME" exec -it "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" ;;
    url)     url ;;
    reset)
        exists && "$RUNTIME" rm -f "$CONTAINER" >/dev/null 2>&1 || true
        "$RUNTIME" volume rm -f "$VOLUME" >/dev/null 2>&1 || true
        echo "container and volume removed" ;;
    *)
        echo "usage: sql/dev-db.sh {start|stop|restart|status|apply|psql|url|reset}" >&2
        exit 1 ;;
esac
