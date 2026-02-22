"""History and settings routes"""

from flask import Blueprint, render_template, request, session, redirect, jsonify
from functools import wraps
from app.utils.db import get_db
import logging

logger = logging.getLogger(__name__)
bp = Blueprint("settings", __name__)


def login_required(f):
    """Decorator to require authentication."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json:
                return jsonify({"error": "Authentication required"}), 401
            return redirect("/login")
        return f(*args, **kwargs)

    return decorated_function


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
        # Build WHERE clause
        where_clauses = ["1=1"]
        params = []

        if search:
            where_clauses.append("h.machine_name LIKE ?")
            params.append(f"%{search}%")

        if change_type:
            where_clauses.append("h.change_type = ?")
            params.append(change_type)

        if date_from:
            where_clauses.append("DATE(h.detected_at) >= ?")
            params.append(date_from)

        where_sql = " AND ".join(where_clauses)

        # Get total count for pagination
        count_query = f"SELECT COUNT(*) FROM vm_history h WHERE {where_sql}"
        result = db.execute(count_query, params).fetchone()
        total_count = result[0] if result else 0
        total_pages = max(1, (total_count + per_page - 1) // per_page)

        # Get paginated results
        offset = (page - 1) * per_page
        query = f"""
            SELECT h.*,
                   (SELECT machine_name FROM vm_info WHERE vm_id = h.vm_id LIMIT 1) as machine_name
            FROM vm_history h
            WHERE {where_sql}
            ORDER BY h.detected_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([per_page, offset])

        history = db.execute(query, params).fetchall()

    # Add styling info
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
    for h in history:
        h_dict = dict(h)
        style = change_styles.get(
            h["change_type"], {"color": "secondary", "icon": "📝"}
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
        # Get all clusters
        clusters = db.execute("""
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
        """).fetchall()

        # Get all account managers
        account_managers = db.execute("""
            SELECT am.*,
                   (SELECT COUNT(*) FROM client_account_managers WHERE manager_id = am.id) as client_count
            FROM account_managers am
            ORDER BY am.name
        """).fetchall()

    return render_template(
        "settings.html",
        clusters=clusters,
        account_managers=account_managers,
    )
