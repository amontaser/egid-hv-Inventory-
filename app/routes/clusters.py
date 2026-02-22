"""Cluster management routes"""

import os
import logging
from flask import (
    Blueprint,
    render_template,
    request,
    session,
    abort,
    redirect,
    flash,
    jsonify,
)
from functools import wraps
from app.utils.db import get_db

logger = logging.getLogger(__name__)
bp = Blueprint("clusters", __name__)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json:
                return jsonify({"error": "Authentication required"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function


def _encrypt_password(password: str) -> str:
    """Encrypt a password using Fernet. Key from ENCRYPTION_KEY env var or key file."""
    from cryptography.fernet import Fernet

    key = os.getenv("ENCRYPTION_KEY", "")
    key_file = "/opt/hyperv_inventory/encryption_key.key"
    if not key and os.path.exists(key_file):
        with open(key_file, "rb") as f:
            key = f.read().decode()
    if not key:
        key = Fernet.generate_key().decode()
        with open(key_file, "wb") as f:
            f.write(key.encode())
        logger.info("Generated new encryption key")

    fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return fernet.encrypt(password.encode()).decode()


@bp.route("/clusters")
@login_required
def cluster_list():
    """List all clusters."""
    with get_db() as db:
        clusters = db.execute("""
            SELECT
                c.*,
                COALESCE(stats.vm_count, 0) as vm_count,
                COALESCE(stats.running_vms, 0) as running_vms
            FROM clusters c
            LEFT JOIN (
                SELECT cluster_name, COUNT(*) as vm_count,
                       SUM(CASE WHEN state = 'Running' THEN 1 ELSE 0 END) as running_vms
                FROM vm_info
                GROUP BY cluster_name
            ) stats ON c.cluster_name = stats.cluster_name
            ORDER BY c.cluster_name
        """).fetchall()

    return render_template("clusters.html", clusters=clusters)


@bp.route("/cluster/add", methods=["GET", "POST"])
@login_required
def add_cluster():
    """Add a new cluster."""
    if request.method == "POST":
        cluster_name = request.form.get("cluster_name", "").strip()
        domain = request.form.get("domain", "").strip()
        cluster_name_for_ps = request.form.get("cluster_name_for_ps", "").strip() or None
        domain_name = request.form.get("domain_name", "").strip() or None
        dns_servers = request.form.get("dns_servers", "").strip() or None
        location = request.form.get("location", "").strip() or None
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        transport = request.form.get("transport", "ntlm")
        require_https = 1 if request.form.get("require_https") else 0
        is_enabled = 1

        if not cluster_name or not domain or not username or not password:
            flash("Cluster name, domain, username and password are required", "error")
            return render_template("cluster_form.html", cluster=request.form, mode="add")

        encrypted_password = _encrypt_password(password)

        with get_db() as db:
            try:
                db.execute(
                    """INSERT INTO clusters (
                        cluster_name, domain, cluster_name_for_ps, domain_name,
                        dns_servers, location, username, password, transport,
                        require_https, is_enabled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (cluster_name, domain, cluster_name_for_ps, domain_name,
                     dns_servers, location, username, encrypted_password, transport,
                     require_https, is_enabled),
                )
                flash("Cluster added successfully", "success")
                return redirect("/settings#clusters")
            except Exception as e:
                flash(f"Error adding cluster: {str(e)}", "error")
                logger.error(f"Error adding cluster: {e}")

    return render_template("cluster_form.html", cluster={}, mode="add")


@bp.route("/cluster/<int:cluster_id>")
@login_required
def cluster_details(cluster_id):
    """View cluster details."""
    with get_db() as db:
        cluster = db.execute(
            "SELECT * FROM clusters WHERE id = ?", (cluster_id,)
        ).fetchone()
        if not cluster:
            abort(404)

        vms = db.execute(
            "SELECT * FROM vm_info WHERE cluster_name = ? ORDER BY machine_name",
            (cluster["cluster_name"],),
        ).fetchall()

        hosts = db.execute(
            "SELECT * FROM hyperv_hosts WHERE cluster_name = ? ORDER BY host_name",
            (cluster["cluster_name"],),
        ).fetchall()

        stats = {
            "vm_count": len(vms),
            "running_vms": sum(1 for vm in vms if vm["state"] == "Running"),
            "host_count": len(hosts),
        }

    return render_template(
        "cluster_detail.html", cluster=cluster, vms=vms, hosts=hosts, stats=stats
    )


@bp.route("/cluster/edit/<int:cluster_id>", methods=["GET", "POST"])
@login_required
def edit_cluster(cluster_id):
    """Edit cluster configuration."""
    with get_db() as db:
        cluster = db.execute(
            "SELECT * FROM clusters WHERE id = ?", (cluster_id,)
        ).fetchone()
        if not cluster:
            abort(404)

    if request.method == "POST":
        cluster_name = request.form.get("cluster_name", "").strip()
        domain = request.form.get("domain", "").strip()
        cluster_name_for_ps = request.form.get("cluster_name_for_ps", "").strip() or None
        domain_name = request.form.get("domain_name", "").strip() or None
        dns_servers = request.form.get("dns_servers", "").strip() or None
        location = request.form.get("location", "").strip() or None
        username = request.form.get("username", "").strip()
        transport = request.form.get("transport", "ntlm")
        require_https = 1 if request.form.get("require_https") else 0
        is_enabled = 1 if request.form.get("is_enabled") else 0
        new_password = request.form.get("password", "")

        with get_db() as db:
            try:
                if new_password:
                    db.execute(
                        """UPDATE clusters SET
                            cluster_name=?, domain=?, cluster_name_for_ps=?, domain_name=?,
                            dns_servers=?, location=?, username=?, password=?,
                            transport=?, require_https=?, is_enabled=?
                           WHERE id=?""",
                        (cluster_name, domain, cluster_name_for_ps, domain_name,
                         dns_servers, location, username, _encrypt_password(new_password),
                         transport, require_https, is_enabled, cluster_id),
                    )
                else:
                    db.execute(
                        """UPDATE clusters SET
                            cluster_name=?, domain=?, cluster_name_for_ps=?, domain_name=?,
                            dns_servers=?, location=?, username=?,
                            transport=?, require_https=?, is_enabled=?
                           WHERE id=?""",
                        (cluster_name, domain, cluster_name_for_ps, domain_name,
                         dns_servers, location, username,
                         transport, require_https, is_enabled, cluster_id),
                    )
                flash("Cluster updated successfully", "success")
                return redirect(f"/cluster/{cluster_id}")
            except Exception as e:
                flash(f"Error updating cluster: {str(e)}", "error")
                logger.error(f"Error updating cluster: {e}")

    return render_template("cluster_form.html", cluster=dict(cluster), mode="edit")


@bp.route("/cluster/<int:cluster_id>/delete", methods=["POST"])
@login_required
def delete_cluster(cluster_id):
    """Delete a cluster."""
    with get_db() as db:
        cluster = db.execute(
            "SELECT * FROM clusters WHERE id = ?", (cluster_id,)
        ).fetchone()
        if not cluster:
            abort(404)

        vm_count = db.execute(
            "SELECT COUNT(*) FROM vm_info WHERE cluster_name = ?",
            (cluster["cluster_name"],),
        ).fetchone()[0]

        if vm_count > 0:
            flash(
                f"Cannot delete cluster with {vm_count} VMs. Remove VMs first.",
                "error",
            )
            return redirect("/settings#clusters")

        db.execute("DELETE FROM clusters WHERE id = ?", (cluster_id,))
        flash("Cluster deleted successfully", "success")

    return redirect("/settings#clusters")


@bp.route("/cluster/delete/<int:cluster_id>", methods=["POST"])
@login_required
def delete_cluster_alt(cluster_id):
    return delete_cluster(cluster_id)


@bp.route("/cluster/test/<int:cluster_id>", methods=["POST"])
@login_required
def test_cluster_connection(cluster_id):
    """Test WinRM connection to a Hyper-V cluster."""
    with get_db() as db:
        cluster = db.execute(
            "SELECT * FROM clusters WHERE id = ?", (cluster_id,)
        ).fetchone()
        if not cluster:
            return jsonify({"success": False, "message": "Cluster not found"}), 404

    try:
        from tasks.hyperv import create_winrm_session, run_powershell

        domain = cluster["domain"] or cluster["cluster_name"]
        session = create_winrm_session(domain, cluster_id=cluster_id)
        result = run_powershell(
            session,
            "Get-ClusterNode | Select-Object -ExpandProperty Name | ConvertTo-Json"
        )
        node_count = len(result) if result else 0
        return jsonify({
            "success": True,
            "message": f"Connected to {domain}. Found {node_count} node(s).",
        })
    except Exception as e:
        logger.error(f"Connection test failed for cluster {cluster_id}: {e}")
        return jsonify({"success": False, "message": f"Connection failed: {str(e)}"})
