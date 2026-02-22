"""API route blueprints"""

from flask import Blueprint, jsonify, request, session, redirect
from functools import wraps
from app.utils.db import get_db
import logging

logger = logging.getLogger(__name__)
bp = Blueprint("api", __name__)


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


def get_all_clusters():
    """Get all clusters with statistics."""
    with get_db() as db:
        return db.execute(
            """
            SELECT 
                c.id,
                c.cluster_name,
                c.location,
                c.is_enabled,
                COALESCE(stats.vm_count, 0) as vm_count,
                COALESCE(stats.running_vms, 0) as running_vms,
                COALESCE(stats.host_count, 0) as host_count
            FROM clusters c
            LEFT JOIN (
                SELECT 
                    cluster_name,
                    COUNT(*) as vm_count,
                    SUM(CASE WHEN state = 'Running' THEN 1 ELSE 0 END) as running_vms,
                    COUNT(DISTINCT host_name) as host_count
                FROM vm_info
                GROUP BY cluster_name
            ) stats ON c.cluster_name = stats.cluster_name
            ORDER BY c.cluster_name
            """
        ).fetchall()


@bp.route("/vms")
@login_required
def list_vms():
    """API: List all VMs with optional filtering."""
    with get_db() as db:
        # Get query parameters
        host = request.args.get("host")
        state = request.args.get("state")
        search = request.args.get("search")

        query = "SELECT * FROM vm_info WHERE 1=1"
        params = []

        if host:
            query += " AND host_name = ?"
            params.append(host)

        if state:
            query += " AND state = ?"
            params.append(state)

        if search:
            query += " AND machine_name LIKE ?"
            params.append(f"%{search}%")

        query += " ORDER BY machine_name"

        vms = db.execute(query, params).fetchall()

        return jsonify({"count": len(vms), "vms": [dict(vm) for vm in vms]})


@bp.route("/vm/<vm_id>")
@login_required
def get_vm(vm_id):
    """API: Get detailed VM information."""
    with get_db() as db:
        vm = db.execute("SELECT * FROM vm_info WHERE vm_id = ?", (vm_id,)).fetchone()
        if not vm:
            return jsonify({"error": "VM not found"}), 404

        disks = db.execute(
            "SELECT * FROM vm_disks WHERE vm_id = ?", (vm_id,)
        ).fetchall()
        networks = db.execute(
            "SELECT * FROM vm_network_adapters WHERE vm_id = ?", (vm_id,)
        ).fetchall()
        snapshots = db.execute(
            "SELECT * FROM vm_snapshots WHERE vm_id = ?", (vm_id,)
        ).fetchall()
        replication = db.execute(
            "SELECT * FROM vm_replication WHERE vm_id = ?", (vm_id,)
        ).fetchone()

        return jsonify(
            {
                "vm": dict(vm),
                "disks": [dict(d) for d in disks],
                "network_adapters": [dict(n) for n in networks],
                "snapshots": [dict(s) for s in snapshots],
                "replication": dict(replication) if replication else None,
            }
        )


@bp.route("/stats")
@login_required
def get_stats():
    """API: Get dashboard statistics."""
    with get_db() as db:
        stats = db.execute("""
            SELECT 
                COUNT(*) as total_vms,
                SUM(CASE WHEN state = 'Running' THEN 1 ELSE 0 END) as running_vms,
                SUM(CASE WHEN state = 'Off' THEN 1 ELSE 0 END) as stopped_vms,
                SUM(cpu_count) as total_vcpus,
                ROUND(SUM(memory_assigned_gb), 2) as total_memory_gb,
                COUNT(DISTINCT host_name) as host_count
            FROM vm_info
        """).fetchone()

        return jsonify(dict(stats))


@bp.route("/clusters")
@login_required
def list_clusters():
    """API: Get all enabled clusters for dropdown."""
    clusters = get_all_clusters()
    return jsonify([dict(c) for c in clusters])


@bp.route("/hosts")
@login_required
def list_hosts():
    """API: List Hyper-V hosts."""
    with get_db() as db:
        hosts = db.execute("SELECT * FROM hyperv_hosts ORDER BY host_name").fetchall()
        return jsonify({"count": len(hosts), "hosts": [dict(h) for h in hosts]})


@bp.route("/sync/status")
@login_required
def sync_status():
    """Get current sync status."""
    with get_db() as db:
        sync_info = db.execute("SELECT * FROM sync_metadata WHERE id = 1").fetchone()

        if not sync_info:
            return jsonify({"status": "never_synced"})

        data = dict(sync_info)

        # Add live VM count so the frontend can show real-time discovery progress
        vm_count = db.execute("SELECT COUNT(*) as cnt FROM vm_info").fetchone()["cnt"]
        data["vms_discovered"] = vm_count

        return jsonify(data)


@bp.route("/notifications/unread-count")
@login_required
def unread_count():
    """Get unread notification count (for AJAX polling)."""
    with get_db() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM notifications WHERE is_read = 0"
        ).fetchone()[0]
        return jsonify({"count": count})
