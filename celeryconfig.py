# Celery Beat Schedule Configuration
# This schedules periodic full sync tasks (VMs + CSV storage)

from celery import Celery
from celery.schedules import crontab

celery = Celery("tasks")

celery.conf.update(
    broker_url="redis://localhost:6379/0",
    result_backend="redis://localhost:6379/0",
    task_routes={
        "hyperv_inventory.tasks.sync.fetch_hyperv_data": {"queue": "hyperv"},
        "hyperv_inventory.tasks.sync.fetch_single_host": {"queue": "hyperv"},
        "hyperv_inventory.tasks.csv_scanner.fetch_cluster_csv_storage": {
            "queue": "csv"
        },
    },
)

beat_schedule = {
    # Full sync (VMs + CSV) daily at midnight
    "full-sync-daily-midnight": {
        "task": "hyperv_inventory.tasks.sync.fetch_hyperv_data",
        "schedule": crontab(hour=0, minute=0),  # Run at midnight (00:00) every day
        "options": {
            "expires": 86400  # Task expires after 24 hours if not picked up
        },
    },
    # Hourly CSV storage scan - runs independently of VM sync
    "csv-scan-hourly": {
        "task": "hyperv_inventory.tasks.csv_scanner.fetch_cluster_csv_storage",
        "schedule": crontab(minute=0),  # Run every hour at :00 minutes
        "options": {
            "expires": 3600  # Task expires after 1 hour if not picked up
        },
    },
}
