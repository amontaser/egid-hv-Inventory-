import pytest
from unittest.mock import patch, MagicMock, call
from tasks.notifications import dispatch_notifications
from tasks.notifications.email import send_email_alerts
from tasks.notifications.webhook import send_webhook_alerts


SAMPLE_EVENTS = [
    {
        "change_type": "state_change",
        "severity": "warning",
        "message": "VM prod-web: Running → Off",
        "vm_id": "vm1",
        "machine_name": "prod-web",
        "cluster_name": "PROD",
        "old_value": "Running",
        "new_value": "Off",
        "detected_at": "2026-02-22 12:00:00",
    },
]


def test_dispatch_calls_email_when_enabled():
    settings = {"enable_email_alerts": "1", "enable_webhook_alerts": "0"}
    with (
        patch("tasks.notifications._load_settings", return_value=settings),
        patch("tasks.notifications.email.send_email_alerts") as mock_email,
    ):
        dispatch_notifications(SAMPLE_EVENTS)
        mock_email.assert_called_once()


def test_dispatch_calls_webhook_when_enabled():
    settings = {"enable_email_alerts": "0", "enable_webhook_alerts": "1"}
    with (
        patch("tasks.notifications._load_settings", return_value=settings),
        patch("tasks.notifications.webhook.send_webhook_alerts") as mock_wh,
    ):
        dispatch_notifications(SAMPLE_EVENTS)
        mock_wh.assert_called_once()


def test_dispatch_skips_both_when_disabled():
    settings = {"enable_email_alerts": "0", "enable_webhook_alerts": "0"}
    with (
        patch("tasks.notifications._load_settings", return_value=settings),
        patch("tasks.notifications.email.send_email_alerts") as mock_email,
        patch("tasks.notifications.webhook.send_webhook_alerts") as mock_wh,
    ):
        dispatch_notifications(SAMPLE_EVENTS)
        mock_email.assert_not_called()
        mock_wh.assert_not_called()


def test_dispatch_empty_events_is_noop():
    with patch("tasks.notifications._load_settings") as mock_settings:
        dispatch_notifications([])
        mock_settings.assert_not_called()


def test_webhook_posts_json():
    settings = {"webhook_url": "http://hooks.example.com/test"}
    with patch("tasks.notifications.webhook.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()
        send_webhook_alerts(SAMPLE_EVENTS, settings)
        assert mock_post.call_count == 1
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["type"] == "state_change"
        assert kwargs["json"]["severity"] == "warning"
