"""VM and client management routes"""

from flask import Blueprint, render_template, request, session, abort, redirect, jsonify
from functools import wraps
from datetime import datetime
from app.utils.db import get_db
from sqlalchemy import text
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
            text("""
            SELECT 
                c.id,
                c.cluster_name,
                c.location,
                c.is_enabled,
                COALESCE(stats.vm_count, 0) as vm_count,
                COALESCE(stats.running_vms, 0) as running_vms,
                COALESCE(stats.host_count, 0) as host_count,
                COALESCE(ns.total_nodes, 0) as total_nodes,
                COALESCE(ns.up_nodes, 0) as up_nodes,
                COALESCE(ns.paused_nodes, 0) as paused_nodes,
                COALESCE(ns.down_nodes, 0) as down_nodes
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
            LEFT JOIN (
                SELECT 
                    cluster_name,
                    COUNT(*) as total_nodes,
                    SUM(CASE WHEN node_state = 'Up' THEN 1 ELSE 0 END) as up_nodes,
                    SUM(CASE WHEN node_state = 'Paused' THEN 1 ELSE 0 END) as paused_nodes,
                    SUM(CASE WHEN node_state = 'Down' THEN 1 ELSE 0 END) as down_nodes
                FROM cluster_nodes
                GROUP BY cluster_name
            ) ns ON c.cluster_name = ns.cluster_name
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


def format_last_update(sync_info):
    """Format the last update time as a human-readable string."""
    if not sync_info or not sync_info._mapping["last_sync_end"]:
        return "Never synced"

    try:
        last_sync = datetime.strptime(
            sync_info._mapping["last_sync_end"], "%Y-%m-%d %H:%M:%S"
        )
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

    cluster_id = get_selected_cluster()
    cluster_name = get_cluster_name(cluster_id)

    if cluster_id:
        session["selected_cluster_id"] = str(cluster_id)
    elif "selected_cluster_id" in session:
        session.pop("selected_cluster_id")

    cluster_filter = ""
    cluster_filter_v = ""
    params = {}
    if cluster_name:
        cluster_filter = "WHERE cluster_name = :cluster_name"
        cluster_filter_v = "WHERE v.cluster_name = :cluster_name"
        params = {"cluster_name": cluster_name}

    all_clusters = get_all_clusters()

    with get_db() as db:
        stats = db.execute(
            text(f"""
            SELECT 
                COUNT(*) as total_vms,
                SUM(CASE WHEN state = 'Running' THEN 1 ELSE 0 END) as running_vms,
                SUM(CASE WHEN state = 'Off' THEN 1 ELSE 0 END) as stopped_vms,
                SUM(cpu_count) as total_vcpus,
                ROUND(SUM(memory_assigned_gb), 2) as total_memory_gb,
                COUNT(DISTINCT host_name) as host_count
            FROM vm_info
            {cluster_filter}
        """),
            params,
        ).fetchone()

        vms_raw = db.execute(
            text(f"""
            SELECT
                v.*,
                (SELECT COUNT(*) FROM vm_disks WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name) as disk_count,
                (SELECT ROUND(SUM(size_gb), 2) FROM vm_disks WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name) as total_disk_gb,
                (SELECT COUNT(*) FROM vm_snapshots WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name) as snapshot_count,
                (SELECT STRING_AGG(ip_addresses, ',') FROM vm_network_adapters WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name AND ip_addresses IS NOT NULL AND ip_addresses != '') as ip_addresses,
                (SELECT STRING_AGG(DISTINCT vlan_id::text, ',') FROM vm_network_adapters WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name AND vlan_id IS NOT NULL AND vlan_id != 0) as vlans,
                h.host_name as host_node_name,
                c.id as client_id,
                c.name as client_name
            FROM vm_info v
            LEFT JOIN hyperv_hosts h ON v.host_name = h.host_name
            LEFT JOIN vm_clients vc ON v.vm_id = vc.vm_id AND v.cluster_name = vc.cluster_name
            LEFT JOIN clients c ON vc.client_id = c.id AND c.state = 1
            {cluster_filter_v}
            ORDER BY v.machine_name
        """),
            params,
        ).fetchall()

        sync_info = db.execute(
            text("SELECT * FROM sync_metadata WHERE id = 1")
        ).fetchone()

    vms = []
    for vm in vms_raw:
        vm_dict = dict(vm._mapping)
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
            abort(404)

        cn = vm._mapping["cluster_name"]
        disks = db.execute(
            text("SELECT * FROM vm_disks WHERE vm_id = :vm_id AND cluster_name = :cn"),
            {"vm_id": vm_id, "cn": cn},
        ).fetchall()
        networks = db.execute(
            text(
                "SELECT * FROM vm_network_adapters WHERE vm_id = :vm_id AND cluster_name = :cn"
            ),
            {"vm_id": vm_id, "cn": cn},
        ).fetchall()
        snapshots = db.execute(
            text(
                "SELECT * FROM vm_snapshots WHERE vm_id = :vm_id AND cluster_name = :cn"
            ),
            {"vm_id": vm_id, "cn": cn},
        ).fetchall()
        replication = db.execute(
            text(
                "SELECT * FROM vm_replication WHERE vm_id = :vm_id AND cluster_name = :cn"
            ),
            {"vm_id": vm_id, "cn": cn},
        ).fetchone()

        client = db.execute(
            text("""
            SELECT c.* FROM clients c
            JOIN vm_clients vc ON c.id = vc.client_id
            WHERE vc.vm_id = :vm_id AND vc.cluster_name = :cn
        """),
            {"vm_id": vm_id, "cn": cn},
        ).fetchone()

        notes = db.execute(
            text("""
            SELECT id, note_text, created_at, updated_at 
            FROM vm_notes 
            WHERE vm_id = :vm_id AND cluster_name = :cn
            ORDER BY created_at DESC
        """),
            {"vm_id": vm_id, "cn": cn},
        ).fetchall()

        history = db.execute(
            text("""
            SELECT * FROM vm_history 
            WHERE vm_id = :vm_id
            ORDER BY detected_at DESC
            LIMIT 20
        """),
            {"vm_id": vm_id},
        ).fetchall()

        all_clients = db.execute(
            text("SELECT * FROM clients WHERE state = 1 ORDER BY name")
        ).fetchall()

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
        h_dict = dict(h._mapping)
        style = change_styles.get(
            h._mapping["change_type"], {"color": "secondary", "icon": "📝"}
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
        vm = db.execute(
            text("SELECT * FROM vm_info WHERE vm_id = :vm_id"), {"vm_id": vm_id}
        ).fetchone()
        if not vm:
            abort(404)

        history = db.execute(
            text("""
            SELECT * FROM vm_history 
            WHERE vm_id = :vm_id
            ORDER BY detected_at DESC
            LIMIT 100
        """),
            {"vm_id": vm_id},
        ).fetchall()

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
        h_dict = dict(h._mapping)
        style = change_styles.get(
            h._mapping["change_type"], {"color": "secondary", "icon": "📝"}
        )
        h_dict.update(style)
        history_with_style.append(h_dict)

    return render_template("vm_history.html", vm=vm, history=history_with_style)


@bp.route("/vm/<vm_id>/notes/add", methods=["POST"])
@login_required
def add_vm_note(vm_id):
    """Add a new note to a VM."""
    note_text = request.form.get("note_text", "").strip()
    cluster_name = request.args.get("cluster")
    if not note_text:
        return jsonify({"success": False, "error": "Note text is required"}), 400

    with get_db() as db:
        if cluster_name:
            vm = db.execute(
                text(
                    "SELECT cluster_name FROM vm_info WHERE vm_id = :vm_id AND cluster_name = :cluster_name"
                ),
                {"vm_id": vm_id, "cluster_name": cluster_name},
            ).fetchone()
        else:
            vm = db.execute(
                text("SELECT cluster_name FROM vm_info WHERE vm_id = :vm_id"),
                {"vm_id": vm_id},
            ).fetchone()
        if not vm:
            return jsonify({"success": False, "error": "VM not found"}), 404

        cn = vm._mapping["cluster_name"]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        note_id = db.execute(
            text(
                "INSERT INTO vm_notes (vm_id, cluster_name, note_text, created_at, updated_at) VALUES (:vm_id, :cn, :note_text, :now, :now) RETURNING id"
            ),
            {"vm_id": vm_id, "cn": cn, "note_text": note_text, "now": now},
        ).scalar()
        db.commit()

        return jsonify({"success": True, "note_id": note_id})


@bp.route("/vm/<vm_id>/notes/<int:note_id>/edit", methods=["POST"])
@login_required
def edit_vm_note(vm_id, note_id):
    """Edit an existing VM note."""
    note_text = request.form.get("note_text", "").strip()
    if not note_text:
        return jsonify({"success": False, "error": "Note text is required"}), 400

    with get_db() as db:
        note = db.execute(
            text("SELECT * FROM vm_notes WHERE id = :note_id AND vm_id = :vm_id"),
            {"note_id": note_id, "vm_id": vm_id},
        ).fetchone()
        if not note:
            return jsonify({"success": False, "error": "Note not found"}), 404

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            text(
                "UPDATE vm_notes SET note_text = :note_text, updated_at = :now WHERE id = :note_id"
            ),
            {"note_text": note_text, "now": now, "note_id": note_id},
        )
        db.commit()

        return jsonify({"success": True})


@bp.route("/vm/<vm_id>/notes/<int:note_id>/delete", methods=["POST"])
@login_required
def delete_vm_note(vm_id, note_id):
    """Delete a VM note."""
    with get_db() as db:
        note = db.execute(
            text("SELECT * FROM vm_notes WHERE id = :note_id AND vm_id = :vm_id"),
            {"note_id": note_id, "vm_id": vm_id},
        ).fetchone()
        if not note:
            return jsonify({"success": False, "error": "Note not found"}), 404

        db.execute(
            text("DELETE FROM vm_notes WHERE id = :note_id"), {"note_id": note_id}
        )
        db.commit()

        return jsonify({"success": True})


@bp.route("/vm/<vm_id>/notes")
@login_required
def get_vm_notes(vm_id):
    """Get all notes for a VM."""
    with get_db() as db:
        notes = db.execute(
            text(
                "SELECT id, note_text, created_at, updated_at FROM vm_notes WHERE vm_id = :vm_id ORDER BY created_at DESC"
            ),
            {"vm_id": vm_id},
        ).fetchall()

        return jsonify([dict(n._mapping) for n in notes])
