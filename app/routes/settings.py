"""History and settings routes"""

from flask import Blueprint, render_template, request, redirect, jsonify
from flask_login import login_required
from app.utils.db import get_db
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)
bp = Blueprint("settings", __name__)


def _reload_celery_beat():
    try:
        import subprocess

        subprocess.Popen(
            [
                "/opt/hyperv_inventory/venv/bin/celery",
                "-A",
                "celeryconfig",
                "beat",
                "--loglevel=INFO",
                "--logfile=/opt/hyperv_inventory/logs/celery-beat.log",
                "--pidfile=/opt/hyperv_inventory/pids/celery-beat.pid",
                "--detach",
            ],
            cwd="/opt/hyperv_inventory",
        )
        logger.info("Restarted celery-beat to pick up new schedule")
    except Exception as e:
        logger.warning(f"Could not restart celery-beat: {e}")


def _row_to_dict(row):
    if row is None:
        return None
    try:
        return dict(row._mapping)
    except Exception:
        return dict(row)


@bp.route("/history")
@login_required
def global_history():
    """View global VM history with filters and pagination."""
    per_page = 50
    page = request.args.get("page", 1, type=int)
    search = request.args.get("search", "")
    change_type = request.args.get("change_type", "")
    date_from = request.args.get("date_from", "")

    with get_db() as db:
        where_clauses = ["1=1"]
        params = {}

        if search:
            where_clauses.append("h.machine_name LIKE :search")
            params["search"] = f"%{search}%"

        if change_type:
            where_clauses.append("h.change_type = :change_type")
            params["change_type"] = change_type

        if date_from:
            where_clauses.append("DATE(h.detected_at) >= :date_from")
            params["date_from"] = date_from

        where_sql = " AND ".join(where_clauses)

        count_query = f"SELECT COUNT(*) FROM vm_history h WHERE {where_sql}"
        result = db.execute(text(count_query), params).fetchone()
        total_count = result[0] if result else 0
        total_pages = max(1, (total_count + per_page - 1) // per_page)

        offset = (page - 1) * per_page
        query = f"""
            SELECT h.*,
                   (SELECT machine_name FROM vm_info WHERE vm_id = h.vm_id LIMIT 1) as machine_name
            FROM vm_history h
            WHERE {where_sql}
            ORDER BY h.detected_at DESC
            LIMIT :per_page OFFSET :offset
        """
        params["per_page"] = per_page
        params["offset"] = offset

        history_raw = db.execute(text(query), params).fetchall()

    change_styles = {
        "created": {"color": "success", "icon": "➕ Created"},
        "deleted": {"color": "danger", "icon": "🗑️ Deleted"},
        "cpu": {"color": "primary", "icon": "⚙️ CPU"},
        "memory": {"color": "primary", "icon": "💾 Memory"},
        "disk": {"color": "info", "icon": "💿 Disk"},
        "ip": {"color": "info", "icon": "🌐 IP"},
        "state": {"color": "warning", "icon": "🔄 State"},
        "host": {"color": "secondary", "icon": "🖥️ Host"},
    }

    history_with_style = []
    for h_row in history_raw:
        h_dict = _row_to_dict(h_row)
        style = change_styles.get(
            h_dict.get("change_type"), {"color": "secondary", "icon": "📝"}
        )
        h_dict.update(style)
        history_with_style.append(h_dict)

    filters = {
        "search": search,
        "change_type": change_type,
        "date_from": date_from,
    }

    return render_template(
        "global_history.html",
        history=history_with_style,
        filters=filters,
        current_page=page,
        total_pages=total_pages,
    )


@bp.route("/settings")
@login_required
def settings():
    """Application settings page."""
    with get_db() as db:
        clusters_raw = db.execute(
            text("""
            SELECT
                c.*,
                '' as domain,
                '' as username,
                'ntlm' as transport,
                '' as dns_servers,
                0 as require_https,
                COALESCE(stats.vm_count, 0) as vm_count
            FROM clusters c
            LEFT JOIN (
                SELECT cluster_name, COUNT(*) as vm_count
                FROM vm_info
                GROUP BY cluster_name
            ) stats ON c.cluster_name = stats.cluster_name
            ORDER BY c.cluster_name
        """)
        ).fetchall()
        clusters = [_row_to_dict(c) for c in clusters_raw]

        account_managers_raw = db.execute(
            text("""
            SELECT am.*,
                   (SELECT COUNT(*) FROM client_account_managers WHERE manager_id = am.id) as client_count
            FROM account_managers am
            ORDER BY am.name
        """)
        ).fetchall()
        account_managers = [_row_to_dict(am) for am in account_managers_raw]

    return render_template(
        "settings.html",
        clusters=clusters,
        account_managers=account_managers,
    )


@bp.route("/settings/schedule", methods=["POST"])
@login_required
def save_schedule():
    """Save sync schedule settings and reload Celery Beat."""
    from datetime import datetime
    import signal

    fields = [
        "sync_enabled",
        "sync_schedule_type",
        "sync_hour",
        "sync_minute",
        "sync_cron_expression",
    ]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as db:
        for field in fields:
            value = request.form.get(field, "")
            db.execute(
                text("""
                INSERT INTO settings (key, value, updated_at) VALUES (:key, :value, :now)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """),
                {"key": field, "value": value, "now": now},
            )
        db.commit()

    _reload_celery_beat()

    return jsonify({"success": True})


@bp.route("/api/schedule/status")
@login_required
def schedule_status():
    """Return current sync status and schedule settings."""
    with get_db() as db:
        sync_row = db.execute(
            text("SELECT * FROM sync_metadata WHERE id = 1")
        ).fetchone()
        sync_meta = _row_to_dict(sync_row) if sync_row else {}

        setting_rows = db.execute(
            text("SELECT key, value FROM settings WHERE key LIKE 'sync_%'")
        ).fetchall()
        schedule = {
            _row_to_dict(r)["key"]: _row_to_dict(r)["value"] for r in setting_rows
        }

    return jsonify({"sync": sync_meta, "schedule": schedule})


@bp.route("/api/schedule/trigger", methods=["POST"])
@login_required
def trigger_sync_now():
    """Trigger an immediate sync."""
    try:
        from celeryconfig import celery as celery_app

        task = celery_app.send_task("tasks.sync.fetch_hyperv_data")
        return jsonify({"success": True, "task_id": task.id})
    except Exception as e:
        logger.error("Failed to trigger sync: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/settings/notifications", methods=["GET", "POST"])
@login_required
def notification_settings():
    """View and update notification settings."""
    from datetime import datetime
    from flask import flash
    from app.db import get_db as get_db_connection

    with get_db_connection() as db:
        if request.method == "POST":
            fields = [
                "storage_threshold_pct",
                "enable_email_alerts",
                "enable_webhook_alerts",
                "webhook_url",
                "alert_email_to",
                "smtp_host",
                "smtp_port",
                "smtp_user",
                "smtp_password",
            ]
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for field in fields:
                value = request.form.get(field, "")
                db.execute(
                    text("""
                    INSERT INTO settings (key, value, updated_at) VALUES (:key, :value, :now)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """),
                    {"key": field, "value": value, "now": now},
                )
            db.commit()
            flash("Notification settings saved.", "success")
            return redirect("/settings/notifications")

        rows = db.execute(text("SELECT key, value FROM settings")).fetchall()
        settings = {_row_to_dict(r)["key"]: _row_to_dict(r)["value"] for r in rows}
        return render_template("notification_settings.html", settings=settings)
