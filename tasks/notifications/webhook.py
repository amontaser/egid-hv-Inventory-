"""Webhook alert delivery (Slack / Teams / generic HTTP)."""

import os
import json
import logging
from typing import List, Dict

import requests

logger = logging.getLogger(__name__)


def send_webhook_alerts(events: List[Dict], settings: Dict[str, str]):
    """POST each event as JSON to the configured webhook URL."""
    url = os.getenv("WEBHOOK_URL") or settings.get("webhook_url", "")
    if not url:
        logger.warning("WEBHOOK_URL not configured — skipping webhook delivery")
        return

    for ev in events:
        payload = {
            "type": ev.get("change_type"),
            "severity": ev.get("severity", "info"),
            "message": ev.get("message"),
            "vm_id": ev.get("vm_id"),
            "machine_name": ev.get("machine_name"),
            "cluster": ev.get("cluster_name"),
            "old_value": ev.get("old_value"),
            "new_value": ev.get("new_value"),
            "timestamp": ev.get("detected_at"),
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Webhook POST failed for event '{ev.get('message')}': {e}")

    logger.info(f"Sent {len(events)} webhook events to {url}")
