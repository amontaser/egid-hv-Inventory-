"""Storage and export routes"""

from flask import (
    Blueprint,
    render_template,
    request,
    session,
    redirect,
    jsonify,
    make_response,
)
from functools import wraps
from datetime import datetime
from app.utils.db import get_db
from sqlalchemy import text
import csv
from io import StringIO
import logging

logger = logging.getLogger(__name__)
bp = Blueprint("storage", __name__)


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


def get_selected_cluster():
    """Get the currently selected cluster from query param or session."""
    cluster_id = request.args.get("cluster_id")

    if not cluster_id or cluster_id == "all" or cluster_id == "":
        if "selected_cluster_id" in session:
            session.pop("selected_cluster_id")
        return None

    return int(cluster_id) if cluster_id.isdigit() else None


def get_cluster_name(cluster_id):
    """Get cluster name by ID."""
    if not cluster_id:
        return None
    with get_db() as db:
        cluster = db.execute(
            text("SELECT cluster_name FROM clusters WHERE id = :cluster_id"),
            {"cluster_id": cluster_id},
        ).fetchone()
        return cluster._mapping["cluster_name"] if cluster else None


@bp.route("/storage")
@login_required
def storage_view():
    """View Cluster Shared Volumes."""
    cluster_id = get_selected_cluster()
    cluster_name = get_cluster_name(cluster_id)

    with get_db() as db:
        csv_scan = db.execute(
            text("SELECT * FROM csv_scan_metadata WHERE id = 1")
        ).fetchone()

        if cluster_name:
            totals = db.execute(
                text("""
                SELECT
                    COUNT(*) as volume_count,
                    COALESCE(SUM(total_size_gb), 0) as total_capacity_gb,
                    COALESCE(SUM(used_space_gb), 0) as total_used_gb,
                    COALESCE(SUM(free_space_gb), 0) as total_free_gb,
                    COALESCE(SUM(vhd_max_size_gb), 0) as total_vhd_max_gb,
                    COALESCE(SUM(vhd_actual_size_gb), 0) as total_vhd_actual_gb
                FROM cluster_shared_volumes
                WHERE cluster_name = :cluster_name
            """),
                {"cluster_name": cluster_name},
            ).fetchone()

            storage = db.execute(
                text("""
                SELECT *
                FROM cluster_shared_volumes
                WHERE cluster_name = :cluster_name
                ORDER BY name
            """),
                {"cluster_name": cluster_name},
            ).fetchall()
        else:
            totals = db.execute(
                text("""
                SELECT
                    COUNT(*) as volume_count,
                    COALESCE(SUM(total_size_gb), 0) as total_capacity_gb,
                    COALESCE(SUM(used_space_gb), 0) as total_used_gb,
                    COALESCE(SUM(free_space_gb), 0) as total_free_gb,
                    COALESCE(SUM(vhd_max_size_gb), 0) as total_vhd_max_gb,
                    COALESCE(SUM(vhd_actual_size_gb), 0) as total_vhd_actual_gb
                FROM cluster_shared_volumes
            """)
            ).fetchone()

            storage = db.execute(
                text("""
                SELECT *
                FROM cluster_shared_volumes
                ORDER BY cluster_name, name
            """)
            ).fetchall()

    totals_dict = dict(totals._mapping)
    if totals_dict["total_capacity_gb"] > 0:
        totals_dict["overall_percent_used"] = round(
            (totals_dict["total_used_gb"] / totals_dict["total_capacity_gb"]) * 100, 1
        )
        totals_dict["overall_oversubscription"] = round(
            (totals_dict["total_vhd_max_gb"] / totals_dict["total_capacity_gb"]) * 100,
            1,
        )
    else:
        totals_dict["overall_percent_used"] = 0
        totals_dict["overall_oversubscription"] = 0

    all_clusters = get_all_clusters()

    return render_template(
        "storage.html",
        storage=storage,
        totals=totals_dict,
        csv_scan=csv_scan,
        clusters=all_clusters,
        selected_cluster_id=cluster_id,
        selected_cluster_name=cluster_name,
    )


@bp.route("/storage/rescan", methods=["POST"])
@login_required
def trigger_storage_rescan():
    """Trigger a forced full sync including CSV storage rescan."""
    from celeryconfig import celery

    with get_db() as db:
        db.execute(
            text("UPDATE csv_scan_metadata SET last_scan_end = NULL WHERE id = 1")
        )
        db.commit()

    task = celery.send_task("tasks.csv_scanner.fetch_cluster_csv_storage")

    if request.is_json:
        return jsonify(
            {
                "status": "started",
                "message": "CSV rescan triggered",
                "task_id": task.id,
            }
        )

    return redirect("/storage")


@bp.route("/update", methods=["POST", "GET"])
@login_required
def trigger_update():
    """Trigger data sync from Hyper-V."""
    from celeryconfig import celery

    task = celery.send_task("tasks.sync.fetch_hyperv_data")

    if request.is_json:
        return jsonify({"status": "started", "task_id": task.id})

    return redirect("/")


@bp.route("/export/csv")
@login_required
def export_csv():
    """Export VM data to CSV."""
    with get_db() as db:
        vms = db.execute(
            text("""
            SELECT
                v.machine_name,
                v.vm_id,
                v.host_name,
                v.cluster_name,
                v.state,
                v.cpu_count,
                v.memory_assigned_gb,
                v.memory_demand_gb,
                v.dynamic_memory_enabled,
                v.generation,
                v.version,
                v.notes,
                (SELECT ROUND(SUM(size_gb), 2) FROM vm_disks WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name) as total_disk_gb,
                (SELECT COUNT(*) FROM vm_disks WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name) as disk_count,
                (SELECT STRING_AGG(ip_addresses, ',') FROM vm_network_adapters WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name) as ip_addresses,
                (SELECT COUNT(*) FROM vm_snapshots WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name) as snapshot_count,
                v.created_at,
                v.updated_at
            FROM vm_info v
            ORDER BY v.machine_name
        """)
        ).fetchall()

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [
            "Machine Name",
            "VM ID",
            "Host",
            "Cluster",
            "State",
            "vCPUs",
            "Memory (GB)",
            "Memory Demand (GB)",
            "Dynamic Memory",
            "Generation",
            "Version",
            "Notes",
            "Total Disk (GB)",
            "Disk Count",
            "IP Addresses",
            "Snapshots",
            "Created",
            "Updated",
        ]
    )

    for vm in vms:
        writer.writerow(
            [
                vm._mapping["machine_name"],
                vm._mapping["vm_id"],
                vm._mapping["host_name"],
                vm._mapping["cluster_name"],
                vm._mapping["state"],
                vm._mapping["cpu_count"],
                vm._mapping["memory_assigned_gb"],
                vm._mapping["memory_demand_gb"],
                "Yes" if vm._mapping["dynamic_memory_enabled"] else "No",
                vm._mapping["generation"],
                vm._mapping["version"],
                vm._mapping["notes"],
                vm._mapping["total_disk_gb"],
                vm._mapping["disk_count"],
                vm._mapping["ip_addresses"],
                vm._mapping["snapshot_count"],
                vm._mapping["created_at"],
                vm._mapping["updated_at"],
            ]
        )

    response = make_response(output.getvalue())
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    response.headers["Content-Disposition"] = (
        f"attachment; filename=HyperV_Inventory_{timestamp}.csv"
    )
    response.headers["Content-Type"] = "text/csv"

    return response
