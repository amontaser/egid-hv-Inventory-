"""Host management routes"""

from flask import Blueprint, render_template, request, session, abort, redirect
from flask_login import login_required
from app.utils.db import get_db
from sqlalchemy import text
from app.utils.db_compat import bool_eq
import logging

logger = logging.getLogger(__name__)
bp = Blueprint("hosts", __name__)


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


@bp.route("/hosts")
@login_required
def host_list():
    """List all Hyper-V hosts grouped by cluster."""
    cluster_id = get_selected_cluster()

    with get_db() as db:
        clusters_raw = db.execute(
            text(
                f"SELECT * FROM clusters WHERE {bool_eq('is_enabled')} ORDER BY cluster_name"
            )
        ).fetchall()

        if cluster_id:
            cluster_row = db.execute(
                text(
                    "SELECT cluster_name, location FROM clusters WHERE id = :cluster_id"
                ),
                {"cluster_id": cluster_id},
            ).fetchone()
            cluster_d = _row_to_dict(cluster_row) if cluster_row else None
            selected_cluster_name = cluster_d["cluster_name"] if cluster_d else None
            cluster_filter = "WHERE cluster_name = :cluster_name"
            params = {"cluster_name": selected_cluster_name}
        else:
            selected_cluster_name = None
            cluster_filter = ""
            params = {}

        hosts_raw = db.execute(
            text(f"""
            SELECT
                COALESCE(hh.host_name, v.host_name) as display_name,
                COALESCE(hh.id, 0) as host_id,
                COALESCE(hh.connection_ip, v.host_name) as connection_ip,
                v.host_name as host_identifier,
                v.cluster_name,
                c.location as cluster_location,
                hh.total_memory_gb,
                hh.available_memory_gb,
                hh.logical_processors,
                hh.os_version,
                hh.vm_count,
                v.actual_vm_count
            FROM (
                SELECT host_name, cluster_name, COUNT(*) as actual_vm_count
                FROM vm_info
                {cluster_filter}
                GROUP BY host_name, cluster_name
            ) v
            LEFT JOIN hyperv_hosts hh ON v.host_name = hh.host_name
            LEFT JOIN clusters c ON v.cluster_name = c.cluster_name
            ORDER BY v.cluster_name, COALESCE(hh.host_name, v.host_name)
        """),
            params,
        ).fetchall()

    clusters_with_hosts = {}
    for host_row in hosts_raw:
        host = _row_to_dict(host_row)
        cluster_name = host["cluster_name"] or "Unknown"
        if cluster_name not in clusters_with_hosts:
            clusters_with_hosts[cluster_name] = {
                "location": host["cluster_location"],
                "hosts": [],
                "total_vms": 0,
                "total_cpus": 0,
                "total_memory_gb": 0,
            }

        host["vm_count"] = host["actual_vm_count"]
        clusters_with_hosts[cluster_name]["hosts"].append(host)
        clusters_with_hosts[cluster_name]["total_vms"] += host["actual_vm_count"] or 0
        clusters_with_hosts[cluster_name]["total_cpus"] += (
            host["logical_processors"] or 0
        )
        clusters_with_hosts[cluster_name]["total_memory_gb"] += (
            host["total_memory_gb"] or 0
        )

    if cluster_id:
        session["selected_cluster_id"] = str(cluster_id)
    elif "selected_cluster_id" in session:
        session.pop("selected_cluster_id")

    return render_template(
        "hosts.html",
        clusters_with_hosts=clusters_with_hosts,
        selected_cluster_name=selected_cluster_name,
        clusters=get_all_clusters(),
    )


@bp.route("/host/<int:host_id>")
@login_required
def host_details(host_id):
    """Detailed view for a single host."""
    with get_db() as db:
        row = db.execute(
            text("SELECT * FROM hyperv_hosts WHERE id = :host_id"), {"host_id": host_id}
        ).fetchone()
        if not row:
            abort(404)
        host = _row_to_dict(row)

        vms_raw = db.execute(
            text(
                "SELECT * FROM vm_info WHERE host_name = :host_name ORDER BY machine_name"
            ),
            {"host_name": host["host_name"]},
        ).fetchall()
        vms = [_row_to_dict(v) for v in vms_raw]

        contacts = []

    return render_template("host_details.html", host=host, contacts=contacts, vms=vms)
