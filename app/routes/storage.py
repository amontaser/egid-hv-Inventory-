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
            "SELECT cluster_name FROM clusters WHERE id = ?", (cluster_id,)
        ).fetchone()
        return cluster["cluster_name"] if cluster else None


@bp.route("/storage")
@login_required
def storage_view():
    """View Cluster Shared Volumes."""
    # Get selected cluster
    cluster_id = get_selected_cluster()
    cluster_name = get_cluster_name(cluster_id)

    with get_db() as db:
        # Get CSV scan metadata
        csv_scan = db.execute("SELECT * FROM csv_scan_metadata WHERE id = 1").fetchone()

        # Build query with cluster filter (join with hyperv_hosts)
        if cluster_name:
            # Filter by cluster through owner_node (case-insensitive join)
            totals = db.execute(
                """
                SELECT
                    COUNT(*) as volume_count,
                    COALESCE(SUM(csv.total_size_gb), 0) as total_capacity_gb,
                    COALESCE(SUM(csv.used_space_gb), 0) as total_used_gb,
                    COALESCE(SUM(csv.free_space_gb), 0) as total_free_gb,
                    COALESCE(SUM(csv.vhd_max_size_gb), 0) as total_vhd_max_gb,
                    COALESCE(SUM(csv.vhd_actual_size_gb), 0) as total_vhd_actual_gb
                FROM cluster_shared_volumes csv
                INNER JOIN hyperv_hosts hh ON LOWER(csv.owner_node) = LOWER(hh.host_name)
                WHERE hh.cluster_name = ?
            """,
                (cluster_name,),
            ).fetchone()

            storage = db.execute(
                """
                SELECT csv.* 
                FROM cluster_shared_volumes csv
                INNER JOIN hyperv_hosts hh ON LOWER(csv.owner_node) = LOWER(hh.host_name)
                WHERE hh.cluster_name = ?
                ORDER BY csv.name
            """,
                (cluster_name,),
            ).fetchall()
        else:
            # No filter - show all, grouped by cluster
            totals = db.execute("""
                SELECT
                    COUNT(*) as volume_count,
                    COALESCE(SUM(total_size_gb), 0) as total_capacity_gb,
                    COALESCE(SUM(used_space_gb), 0) as total_used_gb,
                    COALESCE(SUM(free_space_gb), 0) as total_free_gb,
                    COALESCE(SUM(vhd_max_size_gb), 0) as total_vhd_max_gb,
                    COALESCE(SUM(vhd_actual_size_gb), 0) as total_vhd_actual_gb
                FROM cluster_shared_volumes
            """).fetchone()

            storage = db.execute("""
                SELECT csv.*, hh.cluster_name
                FROM cluster_shared_volumes csv
                LEFT JOIN hyperv_hosts hh ON LOWER(csv.owner_node) = LOWER(hh.host_name)
                ORDER BY hh.cluster_name, csv.name
            """).fetchall()

    # Calculate overall percentage and oversubscription
    totals_dict = dict(totals)
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

    # Get all clusters for dropdown
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

    # Clear CSV cache by updating last_scan_end to NULL
    with get_db() as db:
        db.execute("UPDATE csv_scan_metadata SET last_scan_end = NULL WHERE id = 1")
        db.commit()

    # Trigger CSV scan background task
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
        vms = db.execute("""
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
                (SELECT ROUND(SUM(size_gb), 2) FROM vm_disks WHERE vm_id = v.vm_id) as total_disk_gb,
                (SELECT COUNT(*) FROM vm_disks WHERE vm_id = v.vm_id) as disk_count,
                (SELECT GROUP_CONCAT(ip_addresses) FROM vm_network_adapters WHERE vm_id = v.vm_id) as ip_addresses,
                (SELECT COUNT(*) FROM vm_snapshots WHERE vm_id = v.vm_id) as snapshot_count,
                v.created_at,
                v.updated_at
            FROM vm_info v
            ORDER BY v.machine_name
        """).fetchall()

    output = StringIO()
    writer = csv.writer(output)

    # Header
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
                vm["machine_name"],
                vm["vm_id"],
                vm["host_name"],
                vm["cluster_name"],
                vm["state"],
                vm["cpu_count"],
                vm["memory_assigned_gb"],
                vm["memory_demand_gb"],
                "Yes" if vm["dynamic_memory_enabled"] else "No",
                vm["generation"],
                vm["version"],
                vm["notes"],
                vm["total_disk_gb"],
                vm["disk_count"],
                vm["ip_addresses"],
                vm["snapshot_count"],
                vm["created_at"],
                vm["updated_at"],
            ]
        )

    response = make_response(output.getvalue())
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    response.headers["Content-Disposition"] = (
        f"attachment; filename=HyperV_Inventory_{timestamp}.csv"
    )
    response.headers["Content-Type"] = "text/csv"

    return response
