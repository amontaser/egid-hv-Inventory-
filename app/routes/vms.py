"""VM and client management routes"""

from flask import Blueprint, render_template, request, session, abort, redirect, jsonify
from functools import wraps
from datetime import datetime
from app.utils.db import get_db
import logging

logger = logging.getLogger(__name__)
bp = Blueprint("vms", __name__)


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


def format_last_update(sync_info):
    """Format the last update time as a human-readable string."""
    if not sync_info or not sync_info["last_sync_end"]:
        return "Never synced"

    try:
        last_sync = datetime.strptime(sync_info["last_sync_end"], "%Y-%m-%d %H:%M:%S")
        delta = datetime.now() - last_sync

        if delta.days > 0:
            return f"Last updated {delta.days} day{'s' if delta.days > 1 else ''} ago"

        hours, remainder = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours > 0:
            return f"Last updated {hours}h {minutes}m ago"
        elif minutes > 0:
            return f"Last updated {minutes}m {seconds}s ago"
        else:
            return f"Last updated {seconds}s ago"
    except Exception:
        return "Unknown"


@bp.route("/")
@login_required
def index():
    """Main dashboard view."""

    # Get selected cluster
    cluster_id = get_selected_cluster()
    cluster_name = get_cluster_name(cluster_id)

    # Store selection in session
    if cluster_id:
        session["selected_cluster_id"] = str(cluster_id)
    elif "selected_cluster_id" in session:
        session.pop("selected_cluster_id")

    # Build cluster filter for queries
    cluster_filter = ""
    params = []
    if cluster_name:
        cluster_filter = "WHERE cluster_name = ?"
        params = [cluster_name]

    # Get all clusters for dropdown
    all_clusters = get_all_clusters()

    # Get data from database
    with get_db() as db:
        # Get VM summary statistics
        stats = db.execute(
            f"""
            SELECT 
                COUNT(*) as total_vms,
                SUM(CASE WHEN state = 'Running' THEN 1 ELSE 0 END) as running_vms,
                SUM(CASE WHEN state = 'Off' THEN 1 ELSE 0 END) as stopped_vms,
                SUM(cpu_count) as total_vcpus,
                ROUND(SUM(memory_assigned_gb), 2) as total_memory_gb,
                COUNT(DISTINCT host_name) as host_count
            FROM vm_info
            {cluster_filter}
        """,
            params,
        ).fetchone()

        # Get all VMs with host node names and client info
        vms_raw = db.execute(
            f"""
            SELECT
                v.*,
                (SELECT COUNT(*) FROM vm_disks WHERE vm_id = v.vm_id) as disk_count,
                (SELECT ROUND(SUM(size_gb), 2) FROM vm_disks WHERE vm_id = v.vm_id) as total_disk_gb,
                (SELECT COUNT(*) FROM vm_snapshots WHERE vm_id = v.vm_id) as snapshot_count,
                (SELECT GROUP_CONCAT(ip_addresses) FROM vm_network_adapters WHERE vm_id = v.vm_id AND ip_addresses IS NOT NULL AND ip_addresses != '') as ip_addresses,
                (SELECT GROUP_CONCAT(DISTINCT vlan_id) FROM vm_network_adapters WHERE vm_id = v.vm_id AND vlan_id IS NOT NULL AND vlan_id != 0) as vlans,
                h.host_name as host_node_name,
                c.id as client_id,
                c.name as client_name
            FROM vm_info v
            LEFT JOIN hyperv_hosts h ON v.host_name = h.host_name
            LEFT JOIN vm_clients vc ON v.vm_id = vc.vm_id
            LEFT JOIN clients c ON vc.client_id = c.id AND c.state = 1
            {cluster_filter}
            ORDER BY v.machine_name
        """,
            params,
        ).fetchall()

        # Get last sync info
        sync_info = db.execute("SELECT * FROM sync_metadata WHERE id = 1").fetchone()

    # Process data outside database context
    vms = []
    for vm in vms_raw:
        vm_dict = dict(vm)
        if vm_dict["vlans"]:
            vm_dict["vlan_list"] = sorted([int(v) for v in vm_dict["vlans"].split(",")])
        else:
            vm_dict["vlan_list"] = []
        vms.append(vm_dict)

    last_update_time = format_last_update(sync_info)

    return render_template(
        "index.html",
        stats=stats,
        vms=vms,
        last_update_time=last_update_time,
        sync_info=sync_info,
        clusters=all_clusters,
        selected_cluster_id=cluster_id,
        selected_cluster_name=cluster_name,
    )


@bp.route("/vm/<vm_id>")
@login_required
def vm_details(vm_id):
    """Detailed view for a single VM."""
    with get_db() as db:
        vm = db.execute("SELECT * FROM vm_info WHERE vm_id = ?", (vm_id,)).fetchone()
        if not vm:
            abort(404)

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

        # Get assigned client
        client = db.execute(
            """
            SELECT c.* FROM clients c
            JOIN vm_clients vc ON c.id = vc.client_id
            WHERE vc.vm_id = ?
        """,
            (vm_id,),
        ).fetchone()

        # Get VM notes
        notes = db.execute(
            """
            SELECT id, note_text, created_at, updated_at 
            FROM vm_notes 
            WHERE vm_id = ? 
            ORDER BY created_at DESC
        """,
            (vm_id,),
        ).fetchall()

        # Get history
        history = db.execute(
            """
            SELECT * FROM vm_history 
            WHERE vm_id = ?
            ORDER BY detected_at DESC
            LIMIT 20
        """,
            (vm_id,),
        ).fetchall()

        # Get all active clients for assignment dropdown
        all_clients = db.execute(
            "SELECT * FROM clients WHERE state = 1 ORDER BY name"
        ).fetchall()

    # Add styling (outside database context)
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

    return render_template(
        "vm_details.html",
        vm=vm,
        disks=disks,
        networks=networks,
        snapshots=snapshots,
        replication=replication,
        client=client,
        all_clients=all_clients,
        notes=notes,
        history=history_with_style,
        history_count=len(history_with_style),
    )


@bp.route("/vm/<vm_id>/history")
@login_required
def vm_history(vm_id):
    """View change history for a specific VM."""
    with get_db() as db:
        vm = db.execute("SELECT * FROM vm_info WHERE vm_id = ?", (vm_id,)).fetchone()
        if not vm:
            abort(404)

        history = db.execute(
            """
            SELECT * FROM vm_history 
            WHERE vm_id = ?
            ORDER BY detected_at DESC
            LIMIT 100
        """,
            (vm_id,),
        ).fetchall()

    # Add color/icon info (outside database context)
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

    return render_template("vm_history.html", vm=vm, history=history_with_style)
