"""API route blueprints"""

from flask import Blueprint, jsonify, request, redirect
from flask_login import login_required
from app.utils.db import get_db
from sqlalchemy import text
from app.utils.db_compat import bool_eq
import logging

logger = logging.getLogger(__name__)
bp = Blueprint("api", __name__)


def _row_to_dict(row):
    if row is None:
        return None
    try:
        return dict(row._mapping)
    except Exception:
        return dict(row)


def get_all_clusters():
    """Get all clusters with statistics."""
    with get_db() as db:
        return [
            _row_to_dict(r)
            for r in db.execute(
                text("""
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
                """)
            ).fetchall()
        ]


@bp.route("/vms")
@login_required
def list_vms():
    """API: List all VMs with optional filtering."""
    with get_db() as db:
        host = request.args.get("host")
        state = request.args.get("state")
        search = request.args.get("search")

        query = "SELECT * FROM vm_info WHERE 1=1"
        params = {}

        if host:
            query += " AND host_name = :host"
            params["host"] = host

        if state:
            query += " AND state = :state"
            params["state"] = state

        if search:
            query += " AND machine_name LIKE :search"
            params["search"] = f"%{search}%"

        query += " ORDER BY machine_name"

        vms = db.execute(text(query), params).fetchall()

        return jsonify({"count": len(vms), "vms": [_row_to_dict(vm) for vm in vms]})


@bp.route("/vm/<vm_id>")
@login_required
def get_vm(vm_id):
    """API: Get detailed VM information."""
    cluster_name = request.args.get("cluster")

    with get_db() as db:
        if cluster_name:
            vm = db.execute(
                text(
                    "SELECT * FROM vm_info WHERE vm_id = :vm_id AND cluster_name = :cluster_name"
                ),
                {"vm_id": vm_id, "cluster_name": cluster_name},
            ).fetchone()
        else:
            vm = db.execute(
                text("SELECT * FROM vm_info WHERE vm_id = :vm_id"), {"vm_id": vm_id}
            ).fetchone()
        if not vm:
            return jsonify({"error": "VM not found"}), 404

        vm_d = _row_to_dict(vm)
        cn = vm_d["cluster_name"]
        disks = [
            _row_to_dict(r)
            for r in db.execute(
                text(
                    "SELECT * FROM vm_disks WHERE vm_id = :vm_id AND cluster_name = :cn"
                ),
                {"vm_id": vm_id, "cn": cn},
            ).fetchall()
        ]
        networks = [
            _row_to_dict(r)
            for r in db.execute(
                text(
                    "SELECT * FROM vm_network_adapters WHERE vm_id = :vm_id AND cluster_name = :cn"
                ),
                {"vm_id": vm_id, "cn": cn},
            ).fetchall()
        ]
        snapshots = [
            _row_to_dict(r)
            for r in db.execute(
                text(
                    "SELECT * FROM vm_snapshots WHERE vm_id = :vm_id AND cluster_name = :cn"
                ),
                {"vm_id": vm_id, "cn": cn},
            ).fetchall()
        ]
        rep_row = db.execute(
            text(
                "SELECT * FROM vm_replication WHERE vm_id = :vm_id AND cluster_name = :cn"
            ),
            {"vm_id": vm_id, "cn": cn},
        ).fetchone()
        replication = _row_to_dict(rep_row)

        return jsonify(
            {
                "vm": vm_d,
                "disks": disks,
                "network_adapters": networks,
                "snapshots": snapshots,
                "replication": replication,
            }
        )


@bp.route("/stats")
@login_required
def get_stats():
    """API: Get dashboard statistics."""
    with get_db() as db:
        stats = db.execute(
            text("""
            SELECT 
                COUNT(*) as total_vms,
                SUM(CASE WHEN state = 'Running' THEN 1 ELSE 0 END) as running_vms,
                SUM(CASE WHEN state = 'Off' THEN 1 ELSE 0 END) as stopped_vms,
                SUM(cpu_count) as total_vcpus,
                ROUND(SUM(memory_assigned_gb), 2) as total_memory_gb,
                COUNT(DISTINCT host_name) as host_count
            FROM vm_info
        """)
        ).fetchone()

        return jsonify(_row_to_dict(stats))


@bp.route("/clusters")
@login_required
def list_clusters():
    """API: Get all enabled clusters for dropdown."""
    clusters = get_all_clusters()
    return jsonify(clusters)


@bp.route("/hosts")
@login_required
def list_hosts():
    """API: List Hyper-V hosts."""
    with get_db() as db:
        hosts = db.execute(
            text("SELECT * FROM hyperv_hosts ORDER BY host_name")
        ).fetchall()
        return jsonify({"count": len(hosts), "hosts": [_row_to_dict(h) for h in hosts]})


@bp.route("/sync/status")
@login_required
def sync_status():
    """Get current sync status."""
    with get_db() as db:
        sync_info = db.execute(
            text("SELECT * FROM sync_metadata WHERE id = 1")
        ).fetchone()

        if not sync_info:
            return jsonify({"status": "never_synced"})

        data = _row_to_dict(sync_info)
        vm_row = db.execute(text("SELECT COUNT(*) as cnt FROM vm_info")).fetchone()
        data["vms_discovered"] = _row_to_dict(vm_row)["cnt"]

        return jsonify(data)


@bp.route("/notifications/unread-count")
@login_required
def unread_count():
    """Get unread notification count (for AJAX polling)."""
    with get_db() as db:
        count = db.execute(
            text(
                f"SELECT COUNT(*) FROM notifications WHERE {bool_eq('is_read', val=False)}"
            )
        ).fetchone()[0]
        return jsonify({"count": count})
