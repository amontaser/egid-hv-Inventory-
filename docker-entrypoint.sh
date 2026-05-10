#!/bin/bash
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

wait_for_db() {
    if [ -n "$DATABASE_URL" ]; then
        echo "Waiting for database..."
        for i in $(seq 1 30); do
            python3 -c "import psycopg2; psycopg2.connect('$DATABASE_URL'); print('Database ready')" 2>/dev/null && return 0
            sleep 1
        done
        echo "Warning: database not ready after 30s, continuing anyway"
    fi
}

create_user() {
    if [ "$(id -u)" = "0" ]; then
        groupadd -g "$PGID" appuser 2>/dev/null || true
        useradd -u "$PUID" -g "$PGID" -m appuser 2>/dev/null || true
        chown -R appuser:appuser /opt/hyperv_inventory/data /opt/hyperv_inventory/logs 2>/dev/null || true
        echo "appuser"
    fi
}

wait_for_db

if [ "${SERVICE:-web}" = "web" ]; then
    exec gunicorn --config gunicorn.conf.py wsgi:app
elif [ "$SERVICE" = "celery-worker" ]; then
    EXEC_USER=$(create_user)
    if [ -n "$EXEC_USER" ]; then
        exec gosu "$EXEC_USER" celery -A celeryconfig worker \
            --queues=hyperv,csv \
            --loglevel=INFO \
            --concurrency=4
    else
        exec celery -A celeryconfig worker \
            --queues=hyperv,csv \
            --loglevel=INFO \
            --concurrency=4
    fi
elif [ "$SERVICE" = "celery-beat" ]; then
    EXEC_USER=$(create_user)
    if [ -n "$EXEC_USER" ]; then
        exec gosu "$EXEC_USER" celery -A celeryconfig beat \
            --loglevel=INFO \
            --schedule=/opt/hyperv_inventory/data/celerybeat-schedule \
            --pidfile=/opt/hyperv_inventory/data/celery-beat.pid
    else
        exec celery -A celeryconfig beat \
            --loglevel=INFO \
            --schedule=/opt/hyperv_inventory/data/celerybeat-schedule \
            --pidfile=/opt/hyperv_inventory/data/celery-beat.pid
    fi
else
    exec "$@"
fi
