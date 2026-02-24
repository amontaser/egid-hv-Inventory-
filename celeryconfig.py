# Celery Beat Schedule Configuration

from celery import Celery
from celery.schedules import crontab

celery = Celery("tasks")

celery.conf.update(
    broker_url="redis://localhost:6379/0",
    result_backend="redis://localhost:6379/0",
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
