"""Storage and export routes"""

from flask import (
    Blueprint,
    render_template,
    request,
    session,
    redirect,
    url_for,
    jsonify,
)
from flask_login import login_required
from app.utils.db import get_db
from sqlalchemy import text
from app.utils.db_compat import str_agg
from app.utils.export import build_workbook, workbook_response
import logging

logger = logging.getLogger(__name__)
bp = Blueprint("storage", __name__)


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
        row = db.execute(
            text("SELECT cluster_name FROM clusters WHERE id = :cluster_id"),
            {"cluster_id": cluster_id},
        ).fetchone()
        d = _row_to_dict(row)
        return d["cluster_name"] if d else None


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
        csv_scan = _row_to_dict(csv_scan)

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

            storage_raw = db.execute(
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

            storage_raw = db.execute(
                text("""
                SELECT *
                FROM cluster_shared_volumes
                ORDER BY cluster_name, name
            """)
            ).fetchall()

    totals_dict = _row_to_dict(totals)
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

    storage = [_row_to_dict(s) for s in storage_raw]
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

    task = celery.send_task("tasks.sync.fetch_cluster_csv_storage")

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


@bp.route("/export/vms.xlsx")
@login_required
def export_vms_xlsx():
    cluster_id = request.args.get("cluster_id")
    search = request.args.get("search", "").strip()

    cluster_name = None
    if cluster_id:
        with get_db() as db:
            row = db.execute(
                text("SELECT cluster_name FROM clusters WHERE cluster_id = :cid"),
                {"cid": cluster_id},
            ).fetchone()
            if row:
                cluster_name = row[0]

    where_clauses = []
    params = {}
    if cluster_name:
        where_clauses.append("v.cluster_name = :cluster_name")
        params["cluster_name"] = cluster_name
    if search:
        where_clauses.append(
            "(v.machine_name LIKE :search OR v.host_name LIKE :search OR v.notes LIKE :search)"
        )
        params["search"] = f"%{search}%"

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    with get_db() as db:
        vms_raw = db.execute(
            text(f"""
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
                (SELECT {str_agg("ip_addresses")} FROM vm_network_adapters WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name) as ip_addresses,
                (SELECT COUNT(*) FROM vm_snapshots WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name) as snapshot_count,
                v.created_at,
                v.updated_at
            FROM vm_info v
            {where_sql}
            ORDER BY v.machine_name
        """),
            params,
        ).fetchall()

    headers = [
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

    rows = []
    for vm_row in vms_raw:
        vm = _row_to_dict(vm_row)
        rows.append(
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

    wb = build_workbook([("VMs", headers, rows, None)])
    return workbook_response(wb, "HyperV_Inventory")


@bp.route("/export/csv")
@login_required
def export_csv():
    return redirect(url_for("storage.export_vms_xlsx", **request.args))


@bp.route("/export/storage.xlsx")
@login_required
def export_storage_xlsx():
    cluster_id = request.args.get("cluster_id")
    params = {}
    where = ""
    if cluster_id and cluster_id.isdigit():
        with get_db() as db:
            row = db.execute(
                text("SELECT cluster_name FROM clusters WHERE id = :cid"),
                {"cid": int(cluster_id)},
            ).fetchone()
            if row:
                params["cn"] = row._mapping["cluster_name"]
                where = "WHERE cluster_name = :cn"

    with get_db() as db:
        storage_raw = db.execute(
            text(f"""
            SELECT * FROM cluster_shared_volumes
            {where}
            ORDER BY cluster_name, name
        """),
            params,
        ).fetchall()

    headers = [
        "Volume Name",
        "Cluster",
        "Path",
        "Owner Node",
        "Total (GB)",
        "Used (GB)",
        "Free (GB)",
        "% Used",
        "VHD Count",
        "VHD Max (GB)",
        "VHD Actual (GB)",
        "% Oversubscribed",
    ]
    rows = []
    for s in storage_raw:
        d = _row_to_dict(s)
        pct = (
            round((d["used_space_gb"] / d["total_size_gb"]) * 100, 1)
            if d.get("total_size_gb")
            else 0
        )
        over = (
            round((d["vhd_max_size_gb"] / d["total_size_gb"]) * 100, 1)
            if d.get("total_size_gb")
            else 0
        )
        rows.append(
            [
                d.get("name"),
                d.get("cluster_name"),
                d.get("path"),
                d.get("owner_node"),
                d.get("total_size_gb"),
                d.get("used_space_gb"),
                d.get("free_space_gb"),
                pct,
                d.get("vhd_count"),
                d.get("vhd_max_size_gb"),
                d.get("vhd_actual_size_gb"),
                over,
            ]
        )

    wb = build_workbook([("Storage", headers, rows)])
    return workbook_response(wb, "HyperV_Storage")
