"""Client management routes"""

from flask import (
    Blueprint,
    render_template,
    request,
    abort,
    redirect,
    jsonify,
    flash,
)
from flask_login import login_required
from datetime import datetime
from app.utils.db import get_db
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)
bp = Blueprint("clients", __name__)


def _row_to_dict(row):
    if row is None:
        return None
    try:
        return dict(row._mapping)
    except Exception:
        return dict(row)


@bp.route("/clients")
@login_required
def client_list():
    """List all clients with their VLANs."""
    with get_db() as db:
        clients_raw = db.execute(
            text("""
            SELECT
                c.*,
                (SELECT COUNT(*) FROM vm_clients WHERE client_id = c.id) as vm_count,
                (SELECT GROUP_CONCAT(vlan_id, ',') FROM (
                    SELECT DISTINCT na.vlan_id
                    FROM vm_network_adapters na
                    JOIN vm_clients vc ON na.vm_id = vc.vm_id AND na.cluster_name = vc.cluster_name
                    WHERE vc.client_id = c.id AND na.vlan_id IS NOT NULL AND na.vlan_id != 0
                    ORDER BY na.vlan_id
                )) as vlans
            FROM clients c
            ORDER BY c.name
        """)
        ).fetchall()
        clients = [_row_to_dict(c) for c in clients_raw]
        return render_template("clients.html", clients=clients)


@bp.route("/client/add", methods=["GET", "POST"])
@login_required
def add_client():
    """Add a new client."""
    if request.method == "POST":
        with get_db() as db:
            result = db.execute(
                text(
                    "INSERT INTO clients (name, website, country, description, state) VALUES (:name, :website, :country, :description, :state)"
                ),
                {
                    "name": request.form["name"],
                    "website": request.form.get("website"),
                    "country": request.form.get("country"),
                    "description": request.form.get("description"),
                    "state": int(request.form.get("state", 1)),
                },
            )
            client_id = result.lastrowid

            manager_ids = request.form.getlist("account_managers")
            if manager_ids:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for manager_id in manager_ids:
                    db.execute(
                        text(
                            "INSERT INTO client_account_managers (client_id, manager_id, assigned_at) VALUES (:client_id, :manager_id, :now)"
                        ),
                        {
                            "client_id": client_id,
                            "manager_id": int(manager_id),
                            "now": now,
                        },
                    )

            db.commit()
            return redirect("/clients")

    with get_db() as db:
        managers_raw = db.execute(
            text("SELECT * FROM account_managers WHERE state = 1 ORDER BY name")
        ).fetchall()
        managers = [_row_to_dict(m) for m in managers_raw]
        return render_template("add_client.html", managers=managers)


@bp.route("/client/<int:client_id>")
@login_required
def client_details(client_id):
    """View client details."""
    with get_db() as db:
        row = db.execute(
            text("SELECT * FROM clients WHERE id = :client_id"),
            {"client_id": client_id},
        ).fetchone()
        if not row:
            abort(404)
        client = _row_to_dict(row)

        contacts_raw = db.execute(
            text("SELECT * FROM client_contacts WHERE client_id = :client_id"),
            {"client_id": client_id},
        ).fetchall()
        contacts = [_row_to_dict(c) for c in contacts_raw]

        vms_raw = db.execute(
            text("""
            SELECT v.* FROM vm_info v
            JOIN vm_clients vc ON v.vm_id = vc.vm_id AND v.cluster_name = vc.cluster_name
            WHERE vc.client_id = :client_id
        """),
            {"client_id": client_id},
        ).fetchall()
        vms = [_row_to_dict(v) for v in vms_raw]

        account_managers_raw = db.execute(
            text("""
            SELECT am.* FROM account_managers am
            JOIN client_account_managers cam ON am.id = cam.manager_id
            WHERE cam.client_id = :client_id AND am.state = 1
            ORDER BY am.name
        """),
            {"client_id": client_id},
        ).fetchall()
        account_managers = [_row_to_dict(am) for am in account_managers_raw]

        notes_raw = db.execute(
            text("""
            SELECT id, note_text, created_at, updated_at
            FROM client_notes
            WHERE client_id = :client_id
            ORDER BY created_at DESC
        """),
            {"client_id": client_id},
        ).fetchall()
        notes = [_row_to_dict(n) for n in notes_raw]

        all_managers_raw = db.execute(
            text("SELECT * FROM account_managers WHERE state = 1 ORDER BY name")
        ).fetchall()
        all_managers = [_row_to_dict(m) for m in all_managers_raw]

        return render_template(
            "client_details.html",
            client=client,
            contacts=contacts,
            vms=vms,
            account_managers=account_managers,
            all_managers=all_managers,
            notes=notes,
        )


@bp.route("/client/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def edit_client(client_id):
    """Edit client details."""
    if request.method == "POST":
        with get_db() as db:
            db.execute(
                text(
                    "UPDATE clients SET name=:name, website=:website, country=:country, description=:description, state=:state WHERE id=:client_id"
                ),
                {
                    "name": request.form["name"],
                    "website": request.form.get("website"),
                    "country": request.form.get("country"),
                    "description": request.form.get("description"),
                    "state": int(request.form.get("state", 1)),
                    "client_id": client_id,
                },
            )

            db.execute(
                text(
                    "DELETE FROM client_account_managers WHERE client_id = :client_id"
                ),
                {"client_id": client_id},
            )

            manager_ids = request.form.getlist("account_managers")
            if manager_ids:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for manager_id in manager_ids:
                    db.execute(
                        text(
                            "INSERT INTO client_account_managers (client_id, manager_id, assigned_at) VALUES (:client_id, :manager_id, :now)"
                        ),
                        {
                            "client_id": client_id,
                            "manager_id": int(manager_id),
                            "now": now,
                        },
                    )

            db.commit()
            return redirect(f"/client/{client_id}")

    with get_db() as db:
        row = db.execute(
            text("SELECT * FROM clients WHERE id = :client_id"),
            {"client_id": client_id},
        ).fetchone()
        if not row:
            abort(404)
        client = _row_to_dict(row)

        managers_raw = db.execute(
            text("SELECT * FROM account_managers WHERE state = 1 ORDER BY name")
        ).fetchall()
        managers = [_row_to_dict(m) for m in managers_raw]

        assigned_rows = db.execute(
            text(
                "SELECT manager_id FROM client_account_managers WHERE client_id = :client_id"
            ),
            {"client_id": client_id},
        ).fetchall()
        assigned_manager_ids = [r[0] for r in assigned_rows]

        return render_template(
            "edit_client.html",
            client=client,
            managers=managers,
            assigned_manager_ids=assigned_manager_ids,
        )


@bp.route("/client/<int:client_id>/delete", methods=["POST"])
@login_required
def delete_client(client_id):
    """Delete a client."""
    with get_db() as db:
        db.execute(
            text("DELETE FROM vm_clients WHERE client_id = :client_id"),
            {"client_id": client_id},
        )
        db.execute(
            text("DELETE FROM client_contacts WHERE client_id = :client_id"),
            {"client_id": client_id},
        )
        db.execute(
            text("DELETE FROM clients WHERE id = :client_id"), {"client_id": client_id}
        )
        db.commit()
        return redirect("/clients")


@bp.route("/client/<int:client_id>/contact/add", methods=["POST"])
@login_required
def add_client_contact(client_id):
    """Add a contact for a client."""
    with get_db() as db:
        client = db.execute(
            text("SELECT * FROM clients WHERE id = :client_id"),
            {"client_id": client_id},
        ).fetchone()
        if not client:
            abort(404)

    name = request.form.get("name")
    job_title = request.form.get("job_title")
    email = request.form.get("email")
    phone = request.form.get("phone")
    mobile_phone = request.form.get("mobile_phone")
    is_primary_contact = 1 if request.form.get("is_primary_contact") else 0

    if not name:
        flash("Name is required", "error")
        return redirect(f"/client/{client_id}")

    with get_db() as db:
        if is_primary_contact:
            db.execute(
                text(
                    "UPDATE client_contacts SET is_primary_contact = 0 WHERE client_id = :client_id"
                ),
                {"client_id": client_id},
            )

        db.execute(
            text("""
            INSERT INTO client_contacts (
                client_id, name, job_title, email, phone, mobile_phone, is_primary_contact
            ) VALUES (:client_id, :name, :job_title, :email, :phone, :mobile_phone, :is_primary_contact)
        """),
            {
                "client_id": client_id,
                "name": name,
                "job_title": job_title,
                "email": email,
                "phone": phone,
                "mobile_phone": mobile_phone,
                "is_primary_contact": is_primary_contact,
            },
        )

        db.commit()
        flash("Contact added successfully", "success")
        return redirect(f"/client/{client_id}")


@bp.route("/vm/<vm_id>/assign-client", methods=["POST"])
@login_required
def assign_vm_client(vm_id):
    """Assign a VM to a client."""
    client_id = request.form.get("client_id")
    cluster_name = request.form.get("cluster_name")

    with get_db() as db:
        if not cluster_name:
            vm_row = db.execute(
                text("SELECT cluster_name FROM vm_info WHERE vm_id = :vm_id"),
                {"vm_id": vm_id},
            ).fetchone()
            if vm_row:
                cluster_name = _row_to_dict(vm_row)["cluster_name"]

        db.execute(
            text(
                "DELETE FROM vm_clients WHERE vm_id = :vm_id AND cluster_name = :cluster_name"
            ),
            {"vm_id": vm_id, "cluster_name": cluster_name},
        )

        if client_id:
            db.execute(
                text(
                    "INSERT INTO vm_clients (vm_id, cluster_name, client_id) VALUES (:vm_id, :cluster_name, :client_id)"
                ),
                {"vm_id": vm_id, "cluster_name": cluster_name, "client_id": client_id},
            )

        db.commit()
        redirect_url = f"/vm/{vm_id}"
        if cluster_name:
            redirect_url += f"?cluster={cluster_name}"
        return redirect(redirect_url)


@bp.route("/client/<int:client_id>/notes/add", methods=["POST"])
@login_required
def add_client_note(client_id):
    """Add a new note to a client."""
    note_text = request.form.get("note_text", "").strip()
    with get_db() as db:
        client = db.execute(
            text("SELECT id FROM clients WHERE id = :client_id"),
            {"client_id": client_id},
        ).fetchone()
        if not client:
            return jsonify({"success": False, "error": "Client not found"}), 404

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result = db.execute(
            text(
                "INSERT INTO client_notes (client_id, note_text, created_at, updated_at) VALUES (:client_id, :note_text, :now, :now)"
            ),
            {"client_id": client_id, "note_text": note_text, "now": now},
        )
        note_id = result.lastrowid
        db.commit()

        return jsonify({"success": True, "note_id": note_id})


@bp.route("/client/<int:client_id>/notes/<int:note_id>/edit", methods=["POST"])
@login_required
def edit_client_note(client_id, note_id):
    """Edit an existing client note."""
    note_text = request.form.get("note_text", "").strip()
    with get_db() as db:
        note = db.execute(
            text(
                "SELECT * FROM client_notes WHERE id = :note_id AND client_id = :client_id"
            ),
            {"note_id": note_id, "client_id": client_id},
        ).fetchone()
        if not note:
            return jsonify({"success": False, "error": "Note not found"}), 404

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            text(
                "UPDATE client_notes SET note_text = :note_text, updated_at = :now WHERE id = :note_id"
            ),
            {"note_text": note_text, "now": now, "note_id": note_id},
        )
        db.commit()

        return jsonify({"success": True})


@bp.route("/client/<int:client_id>/notes/<int:note_id>/delete", methods=["POST"])
@login_required
def delete_client_note(client_id, note_id):
    """Delete a client note."""
    with get_db() as db:
        note = db.execute(
            text(
                "SELECT * FROM client_notes WHERE id = :note_id AND client_id = :client_id"
            ),
            {"note_id": note_id, "client_id": client_id},
        ).fetchone()
        if not note:
            return jsonify({"success": False, "error": "Note not found"}), 404

        db.execute(
            text("DELETE FROM client_notes WHERE id = :note_id"), {"note_id": note_id}
        )
        db.commit()

        return jsonify({"success": True})


@bp.route("/client/<int:client_id>/notes")
@login_required
def get_client_notes(client_id):
    """Get all notes for a client."""
    with get_db() as db:
        notes = db.execute(
            text(
                "SELECT id, note_text, created_at, updated_at FROM client_notes WHERE client_id = :client_id ORDER BY created_at DESC"
            ),
            {"client_id": client_id},
        ).fetchall()

        return jsonify([_row_to_dict(n) for n in notes])
