"""Notification delivery: in-app (DB), email, webhook."""

import logging
from typing import List, Dict

from app.db import get_db_connection

logger = logging.getLogger(__name__)


def dispatch_notifications(events: List[Dict]):
    """Deliver change events via all configured channels.

    In-app: always (written by monitor.persist_events — call that first).
    Email: if enable_email_alerts=1 and SMTP configured.
    Webhook: if enable_webhook_alerts=1 and WEBHOOK_URL configured.
    """
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
        conn = get_db_connection()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
        return {r["key"]: r["value"] for r in rows}
    except Exception:
        return {}
