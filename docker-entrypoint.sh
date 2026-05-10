#!/bin/bash
set -e

# Ensure Docker's internal DNS is used for service name resolution
# (ipvlan overrides /etc/resolv.conf with host DNS, breaking container name resolution)
if ! grep -q "127.0.0.11" /etc/resolv.conf 2>/dev/null; then
    sed -i '1i\nameserver 127.0.0.11' /etc/resolv.conf
fi

PUID=${PUID:-1000}
PGID=${PGID:-1000}

create_user() {
    if [ "$(id -u)" = "0" ]; then
        groupadd -g "$PGID" appuser 2>/dev/null || true
        useradd -u "$PUID" -g "$PGID" -m appuser 2>/dev/null || true
        chown -R appuser:appuser /opt/hyperv_inventory/data /opt/hyperv_inventory/logs 2>/dev/null || true
        echo "appuser"
    fi
}

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
