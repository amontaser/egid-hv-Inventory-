"""Notifications routes"""

from flask import Blueprint, render_template, request, session, redirect, flash
from functools import wraps
from app.utils.db import get_db
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)
bp = Blueprint("notifications", __name__)


def login_required(f):
    """Decorator to require authentication."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/login")
        return f(*args, **kwargs)

    return decorated_function


@bp.route("/notifications")
@login_required
def list_notifications():
    """Display all notifications with optional filtering."""
    with get_db() as db:
        unread_only = request.args.get("unread", "0") == "1"
        severity_filter = request.args.get("severity")

        query = "SELECT * FROM notifications WHERE 1=1"
        params = {}

        if unread_only:
            query += " AND is_read = 0"

        if severity_filter:
            query += " AND severity = :severity"
            params["severity"] = severity_filter

        query += " ORDER BY created_at DESC"

        notifications = db.execute(text(query), params).fetchall()

        unread_count = db.execute(
            text("SELECT COUNT(*) FROM notifications WHERE is_read = 0")
        ).fetchone()[0]

        return render_template(
            "notifications.html",
            notifications=notifications,
            unread_count=unread_count,
        )


@bp.route("/notifications/mark-all-read", methods=["POST"])
@login_required
def mark_all_read():
    """Mark all notifications as read."""
    with get_db() as db:
        db.execute(text("UPDATE notifications SET is_read = 1 WHERE is_read = 0"))
        db.commit()
        flash("All notifications marked as read", "success")
        return redirect("/notifications")


@bp.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_read(notification_id):
    """Mark a specific notification as read."""
    with get_db() as db:
        db.execute(
            text("UPDATE notifications SET is_read = 1 WHERE id = :notification_id"),
            {"notification_id": notification_id},
        )
        db.commit()
        flash("Notification marked as read", "success")
        return redirect("/notifications")
