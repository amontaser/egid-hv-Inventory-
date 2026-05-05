"""Email alert delivery via SMTP."""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict

logger = logging.getLogger(__name__)

SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟡", "info": "🔵"}


def send_email_alerts(events: List[Dict], settings: Dict[str, str]):
    """Send one email summarizing all change events."""
    smtp_host = os.getenv("SMTP_HOST") or settings.get("smtp_host", "")
    smtp_port = int(os.getenv("SMTP_PORT") or settings.get("smtp_port", "587"))
    smtp_user = os.getenv("SMTP_USER") or settings.get("smtp_user", "")
    smtp_pass = os.getenv("SMTP_PASSWORD") or settings.get("smtp_password", "")
    to_addr = os.getenv("ALERT_EMAIL_TO") or settings.get("alert_email_to", "")

    if not all([smtp_host, smtp_user, smtp_pass, to_addr]):
        logger.warning(
            "Email not configured — skipping (set SMTP_HOST, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO)"
        )
        return

    subject = f"[HyperV Inventory] {len(events)} change(s) detected"

    lines = []
    for ev in events:
        emoji = SEVERITY_EMOJI.get(ev.get("severity", "info"), "🔵")
        lines.append(f"{emoji} {ev['message']}")

    body = "\n".join(lines)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to_addr], msg.as_string())

    logger.info(f"Sent email alert with {len(events)} events to {to_addr}")
