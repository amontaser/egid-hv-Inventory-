"""Notification delivery: in-app (DB), email, webhook."""

import logging
from typing import List, Dict

from sqlalchemy import text

from app.utils.db import get_db

logger = logging.getLogger(__name__)


def dispatch_notifications(events: List[Dict]):
    if not events:
        return

    settings = _load_settings()

    if settings.get("enable_email_alerts") == "1":
        from .email import send_email_alerts

        try:
            send_email_alerts(events, settings)
        except Exception as e:
            logger.error(f"Email delivery failed: {e}")

    if settings.get("enable_webhook_alerts") == "1":
        from .webhook import send_webhook_alerts

        try:
            send_webhook_alerts(events, settings)
        except Exception as e:
            logger.error(f"Webhook delivery failed: {e}")


def _load_settings() -> Dict[str, str]:
    try:
        with get_db() as db:
            rows = db.execute(text("SELECT key, value FROM settings")).fetchall()
            return {dict(r._mapping)["key"]: dict(r._mapping)["value"] for r in rows}
    except Exception:
        return {}
