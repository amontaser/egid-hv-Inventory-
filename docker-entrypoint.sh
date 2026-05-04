#!/bin/bash
set -e

if [ "${SERVICE:-web}" = "web" ]; then
    exec gunicorn --config gunicorn.conf.py wsgi:app
elif [ "$SERVICE" = "celery-worker" ]; then
    exec celery -A celeryconfig worker \
        --queues=hyperv,csv \
        --loglevel=INFO \
        --concurrency=4
elif [ "$SERVICE" = "celery-beat" ]; then
    exec celery -A celeryconfig beat \
        --loglevel=INFO \
        --pidfile=/opt/hyperv_inventory/data/celery-beat.pid
else
    exec "$@"
fi
