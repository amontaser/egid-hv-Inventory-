"""Host management routes"""

from flask import Blueprint, render_template, request, session, abort, redirect
from functools import wraps
from app.utils.db import get_db
import logging

logger = logging.getLogger(__name__)
bp = Blueprint("hosts", __name__)


def login_required(f):
    """Decorator to require authentication."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
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


@bp.route("/hosts")
@login_required
def host_list():
    """List all Hyper-V hosts grouped by cluster."""
    # Get selected cluster
    cluster_id = get_selected_cluster()

    with get_db() as db:
        # Get all clusters
        clusters = db.execute(
            "SELECT * FROM clusters WHERE is_enabled = 1 ORDER BY cluster_name"
        ).fetchall()

        # Build cluster filter
        if cluster_id:
            cluster = db.execute(
                "SELECT cluster_name, location FROM clusters WHERE id = ?",
                (cluster_id,),
            ).fetchone()
            selected_cluster_name = cluster["cluster_name"] if cluster else None
            cluster_filter = "AND hh.cluster_name = ?"
            params = [selected_cluster_name]
        else:
            selected_cluster_name = None
            cluster_filter = ""
            params = []

        # Get all hosts with VM counts
        hosts = db.execute(
            f"""
            SELECT 
                hh.*,
                c.location as cluster_location
            FROM hyperv_hosts hh
            LEFT JOIN clusters c ON hh.cluster_name = c.cluster_name
            WHERE 1=1 {cluster_filter}
            ORDER BY hh.cluster_name, hh.host_name
        """,
            params,
        ).fetchall()

    # Group hosts by cluster
    clusters_with_hosts = {}
    for host in hosts:
        cluster_name = host["cluster_name"] or "Unknown"
        if cluster_name not in clusters_with_hosts:
            clusters_with_hosts[cluster_name] = {
                "location": host["cluster_location"],
                "hosts": [],
                "total_vms": 0,
                "total_cpus": 0,
                "total_memory_gb": 0,
            }

        host_dict = dict(host)
        clusters_with_hosts[cluster_name]["hosts"].append(host_dict)
        clusters_with_hosts[cluster_name]["total_vms"] += host["vm_count"] or 0
        clusters_with_hosts[cluster_name]["total_cpus"] += (
            host["logical_processors"] or 0
        )
        clusters_with_hosts[cluster_name]["total_memory_gb"] += (
            host["total_memory_gb"] or 0
        )

    # Store selection in session
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
        host = db.execute(
            "SELECT * FROM hyperv_hosts WHERE id = ?", (host_id,)
        ).fetchone()
        if not host:
            abort(404)

        # Get VMs on this host
        vms = db.execute(
            "SELECT * FROM vm_info WHERE host_name = ? ORDER BY machine_name",
            (host["host_name"],),
        ).fetchall()

        # Get contacts (placeholder - no host_contacts table exists yet)
        contacts = []

    return render_template("host_details.html", host=host, contacts=contacts, vms=vms)
