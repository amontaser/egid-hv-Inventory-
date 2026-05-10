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


def _load_schedule_from_db():
    try:
        import sqlite3

        db_path = os.getenv(
            "DATABASE_PATH", os.path.join(os.path.dirname(__file__), "database.db")
        )
        if not os.path.isabs(db_path):
            db_path = os.path.join(os.path.dirname(__file__), db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'sync_%'"
        ).fetchall()
        settings = {row["key"]: row["value"] for row in rows}
        conn.close()

        enabled = settings.get("sync_enabled", "0") == "1"
        if not enabled:
            return {}

        hour = int(settings.get("sync_hour", "0"))
        minute = int(settings.get("sync_minute", "0"))
        sched_type = settings.get("sync_schedule_type", "daily")

        if sched_type == "hourly":
            schedule = crontab(minute=minute)
        elif sched_type == "weekly":
            schedule = crontab(day_of_week=1, hour=hour, minute=minute)
        else:
            schedule = crontab(hour=hour, minute=minute)

        return {
            "full-sync-scheduled": {
                "task": "tasks.sync.fetch_hyperv_data",
                "schedule": schedule,
                "options": {"expires": 86400},
            },
        }
    except Exception:
        return {}


beat_schedule = _load_schedule_from_db()

if not beat_schedule:
    beat_schedule = {
        "full-sync-daily-midnight": {
            "task": "tasks.sync.fetch_hyperv_data",
            "schedule": crontab(hour=0, minute=0),
            "options": {"expires": 86400},
        },
    }

celery.conf.beat_schedule = beat_schedule
