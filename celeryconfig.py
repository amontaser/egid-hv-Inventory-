# Celery Beat Schedule Configuration

import os
import sys

sys.path.insert(0, "/opt/hyperv_inventory")

from celery import Celery
from celery.schedules import crontab

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery = Celery("tasks")

celery.conf.update(
    broker_url=redis_url,
    result_backend=redis_url,
    include=["tasks.orchestrator"],
    task_routes={
        "tasks.sync.fetch_hyperv_data": {"queue": "hyperv"},
        "tasks.sync.fetch_single_host": {"queue": "hyperv"},
        "tasks.sync.aggregate_sync_results_with_csv": {"queue": "hyperv"},
        "tasks.sync.fetch_cluster_csv_storage": {"queue": "csv"},
    },
)

beat_schedule = {
    # Full sync (VMs + CSV) daily at midnight
    "full-sync-daily-midnight": {
        "task": "tasks.sync.fetch_hyperv_data",
        "schedule": crontab(hour=0, minute=0),
        "options": {"expires": 86400},
    },
}

celery.conf.beat_schedule = beat_schedule
