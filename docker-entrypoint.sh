#!/bin/bash
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

if [ "$(id -u)" = "0" ]; then
    groupadd -g "$PGID" appuser 2>/dev/null || true
    useradd -u "$PUID" -g "$PGID" -m appuser 2>/dev/null || true
    chown -R appuser:appuser /opt/hyperv_inventory/data /opt/hyperv_inventory/logs 2>/dev/null || true
    EXEC_USER="appuser"
else
    EXEC_USER=""
fi

run_as() {
    if [ -n "$EXEC_USER" ]; then
        exec gosu "$EXEC_USER" "$@"
    else
        exec "$@"
    fi
}

if [ "${SERVICE:-web}" = "web" ]; then
    run_as gunicorn --config gunicorn.conf.py wsgi:app
elif [ "$SERVICE" = "celery-worker" ]; then
    run_as celery -A celeryconfig worker \
        --queues=hyperv,csv \
        --loglevel=INFO \
        --concurrency=4
elif [ "$SERVICE" = "celery-beat" ]; then
    run_as celery -A celeryconfig beat \
        --loglevel=INFO \
        --pidfile=/opt/hyperv_inventory/data/celery-beat.pid
else
    exec "$@"
fi
