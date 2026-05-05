"""Cluster management routes"""

import os
import logging
from flask import (
    Blueprint,
    render_template,
    request,
    abort,
    redirect,
    flash,
    jsonify,
)
from flask_login import login_required
from app.utils.db import get_db
from sqlalchemy import text

logger = logging.getLogger(__name__)
bp = Blueprint("clusters", __name__)


def _row_to_dict(row):
    if row is None:
        return None
    try:
        return dict(row._mapping)
    except Exception:
        return dict(row)


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
        clusters_raw = db.execute(
            text("""
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
        """)
        ).fetchall()
        clusters = [_row_to_dict(c) for c in clusters_raw]

    return render_template("clusters.html", clusters=clusters)


@bp.route("/cluster/add", methods=["GET", "POST"])
@login_required
def add_cluster():
    """Add a new cluster."""
    if request.method == "POST":
        cluster_name = request.form.get("cluster_name", "").strip()
        domain = request.form.get("domain", "").strip()
        cluster_name_for_ps = (
            request.form.get("cluster_name_for_ps", "").strip() or None
        )
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
            return render_template(
                "cluster_form.html", cluster=request.form, mode="add"
            )

        encrypted_password = _encrypt_password(password)

        with get_db() as db:
            try:
                db.execute(
                    text("""INSERT INTO clusters (
                        cluster_name, domain, cluster_name_for_ps, domain_name,
                        dns_servers, location, username, password, transport,
                        require_https, is_enabled
                    ) VALUES (:cluster_name, :domain, :cluster_name_for_ps, :domain_name,
                              :dns_servers, :location, :username, :password, :transport,
                              :require_https, :is_enabled)"""),
                    {
                        "cluster_name": cluster_name,
                        "domain": domain,
                        "cluster_name_for_ps": cluster_name_for_ps,
                        "domain_name": domain_name,
                        "dns_servers": dns_servers,
                        "location": location,
                        "username": username,
                        "password": encrypted_password,
                        "transport": transport,
                        "require_https": require_https,
                        "is_enabled": is_enabled,
                    },
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
        row = db.execute(
            text("SELECT * FROM clusters WHERE id = :cluster_id"),
            {"cluster_id": cluster_id},
        ).fetchone()
        if not row:
            abort(404)
        cluster = _row_to_dict(row)
        cn = cluster["cluster_name"]

        vms_raw = db.execute(
            text(
                "SELECT * FROM vm_info WHERE cluster_name = :cn ORDER BY machine_name"
            ),
            {"cn": cn},
        ).fetchall()
        vms = [_row_to_dict(v) for v in vms_raw]

        hosts_raw = db.execute(
            text(
                "SELECT * FROM hyperv_hosts WHERE cluster_name = :cn ORDER BY host_name"
            ),
            {"cn": cn},
        ).fetchall()
        hosts = [_row_to_dict(h) for h in hosts_raw]

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
        row = db.execute(
            text("SELECT * FROM clusters WHERE id = :cluster_id"),
            {"cluster_id": cluster_id},
        ).fetchone()
        if not row:
            abort(404)
        cluster = _row_to_dict(row)

    if request.method == "POST":
        cluster_name = request.form.get("cluster_name", "").strip()
        domain = request.form.get("domain", "").strip()
        cluster_name_for_ps = (
            request.form.get("cluster_name_for_ps", "").strip() or None
        )
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
                        text("""UPDATE clusters SET
                            cluster_name=:cluster_name, domain=:domain, cluster_name_for_ps=:cluster_name_for_ps, domain_name=:domain_name,
                            dns_servers=:dns_servers, location=:location, username=:username, password=:password,
                            transport=:transport, require_https=:require_https, is_enabled=:is_enabled
                           WHERE id=:cluster_id"""),
                        {
                            "cluster_name": cluster_name,
                            "domain": domain,
                            "cluster_name_for_ps": cluster_name_for_ps,
                            "domain_name": domain_name,
                            "dns_servers": dns_servers,
                            "location": location,
                            "username": username,
                            "password": _encrypt_password(new_password),
                            "transport": transport,
                            "require_https": require_https,
                            "is_enabled": is_enabled,
                            "cluster_id": cluster_id,
                        },
                    )
                else:
                    db.execute(
                        text("""UPDATE clusters SET
                            cluster_name=:cluster_name, domain=:domain, cluster_name_for_ps=:cluster_name_for_ps, domain_name=:domain_name,
                            dns_servers=:dns_servers, location=:location, username=:username,
                            transport=:transport, require_https=:require_https, is_enabled=:is_enabled
                           WHERE id=:cluster_id"""),
                        {
                            "cluster_name": cluster_name,
                            "domain": domain,
                            "cluster_name_for_ps": cluster_name_for_ps,
                            "domain_name": domain_name,
                            "dns_servers": dns_servers,
                            "location": location,
                            "username": username,
                            "transport": transport,
                            "require_https": require_https,
                            "is_enabled": is_enabled,
                            "cluster_id": cluster_id,
                        },
                    )
                flash("Cluster updated successfully", "success")
                return redirect(f"/cluster/{cluster_id}")
            except Exception as e:
                flash(f"Error updating cluster: {str(e)}", "error")
                logger.error(f"Error updating cluster: {e}")

    return render_template("cluster_form.html", cluster=cluster, mode="edit")


@bp.route("/cluster/<int:cluster_id>/delete", methods=["POST"])
@login_required
def delete_cluster(cluster_id):
    """Delete a cluster."""
    with get_db() as db:
        row = db.execute(
            text("SELECT * FROM clusters WHERE id = :cluster_id"),
            {"cluster_id": cluster_id},
        ).fetchone()
        if not row:
            abort(404)
        cluster = _row_to_dict(row)

        vm_count = db.execute(
            text("SELECT COUNT(*) FROM vm_info WHERE cluster_name = :cluster_name"),
            {"cluster_name": cluster["cluster_name"]},
        ).fetchone()[0]

        if vm_count > 0:
            flash(
                f"Cannot delete cluster with {vm_count} VMs. Remove VMs first.",
                "error",
            )
            return redirect("/settings#clusters")

        db.execute(
            text("DELETE FROM clusters WHERE id = :cluster_id"),
            {"cluster_id": cluster_id},
        )
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
        row = db.execute(
            text("SELECT * FROM clusters WHERE id = :cluster_id"),
            {"cluster_id": cluster_id},
        ).fetchone()
        if not row:
            return jsonify({"success": False, "message": "Cluster not found"}), 404
        cluster = _row_to_dict(row)

    try:
        from tasks.hyperv import create_winrm_session, run_powershell

        domain = cluster["domain"] or cluster["cluster_name"]
        session = create_winrm_session(domain, cluster_id=cluster_id)
        result = run_powershell(
            session,
            "Get-ClusterNode | Select-Object -ExpandProperty Name | ConvertTo-Json",
        )
        node_count = len(result) if result else 0
        return jsonify(
            {
                "success": True,
                "message": f"Connected to {domain}. Found {node_count} node(s).",
            }
        )
    except Exception as e:
        logger.error(f"Connection test failed for cluster {cluster_id}: {e}")
        return jsonify({"success": False, "message": f"Connection failed: {str(e)}"})
