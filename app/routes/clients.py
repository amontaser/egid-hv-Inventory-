"""Client management routes"""

from flask import (
    Blueprint,
    render_template,
    request,
    session,
    abort,
    redirect,
    jsonify,
    flash,
)
from functools import wraps
from datetime import datetime
from hyperv_inventory.app.utils.db import get_db
import logging

logger = logging.getLogger(__name__)
bp = Blueprint("clients", __name__)


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


@bp.route("/clients")
@login_required
def client_list():
    """List all clients with their VLANs."""
    db = get_db()
    clients = db.execute("""
        SELECT
            c.*,
            (SELECT COUNT(*) FROM vm_clients WHERE client_id = c.id) as vm_count,
            (SELECT GROUP_CONCAT(DISTINCT na.vlan_id)
             FROM vm_network_adapters na
             JOIN vm_clients vc ON na.vm_id = vc.vm_id
             WHERE vc.client_id = c.id AND na.vlan_id IS NOT NULL AND na.vlan_id != 0
             GROUP BY na.vlan_id
             ORDER BY na.vlan_id) as vlans
        FROM clients c
        ORDER BY c.name
    """).fetchall()
    return render_template("clients.html", clients=clients)


@bp.route("/client/add", methods=["GET", "POST"])
@login_required
def add_client():
    """Add a new client."""
    if request.method == "POST":
        db = get_db()
        cursor = db.cursor()

        # Insert client
        cursor.execute(
            "INSERT INTO clients (name, website, country, description, state) VALUES (?, ?, ?, ?, ?)",
            (
                request.form["name"],
                request.form.get("website"),
                request.form.get("country"),
                request.form.get("description"),
                int(request.form.get("state", 1)),
            ),
        )
        client_id = cursor.lastrowid

        # Handle account manager assignments
        manager_ids = request.form.getlist("account_managers")
        if manager_ids:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for manager_id in manager_ids:
                cursor.execute(
                    "INSERT INTO client_account_managers (client_id, manager_id, assigned_at) VALUES (?, ?, ?)",
                    (client_id, int(manager_id), now),
                )

        db.commit()
        return redirect("/clients")

    # GET: Fetch all account managers for the form
    db = get_db()
    managers = db.execute(
        "SELECT * FROM account_managers WHERE state = 1 ORDER BY name"
    ).fetchall()
    return render_template("add_client.html", managers=managers)


@bp.route("/client/<int:client_id>")
@login_required
def client_details(client_id):
    """View client details."""
    db = get_db()

    client = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not client:
        abort(404)

    contacts = db.execute(
        "SELECT * FROM client_contacts WHERE client_id = ?", (client_id,)
    ).fetchall()

    vms = db.execute(
        """
        SELECT v.* FROM vm_info v
        JOIN vm_clients vc ON v.vm_id = vc.vm_id
        WHERE vc.client_id = ?
    """,
        (client_id,),
    ).fetchall()

    # Get assigned account managers
    account_managers = db.execute(
        """
        SELECT am.* FROM account_managers am
        JOIN client_account_managers cam ON am.id = cam.manager_id
        WHERE cam.client_id = ? AND am.state = 1
        ORDER BY am.name
    """,
        (client_id,),
    ).fetchall()

    # Get client notes
    notes = db.execute(
        """
        SELECT id, note_text, created_at, updated_at 
        FROM client_notes 
        WHERE client_id = ? 
        ORDER BY created_at DESC
    """,
        (client_id,),
    ).fetchall()

    # Get all available account managers
    all_managers = db.execute("""
        SELECT * FROM account_managers WHERE state = 1 ORDER BY name
    """).fetchall()

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
    db = get_db()

    if request.method == "POST":
        cursor = db.cursor()

        # Update client details
        cursor.execute(
            "UPDATE clients SET name=?, website=?, country=?, description=?, state=? WHERE id=?",
            (
                request.form["name"],
                request.form.get("website"),
                request.form.get("country"),
                request.form.get("description"),
                int(request.form.get("state", 1)),
                client_id,
            ),
        )

        # Update account manager assignments (delete old ones, add new ones)
        cursor.execute(
            "DELETE FROM client_account_managers WHERE client_id = ?", (client_id,)
        )

        manager_ids = request.form.getlist("account_managers")
        if manager_ids:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for manager_id in manager_ids:
                cursor.execute(
                    "INSERT INTO client_account_managers (client_id, manager_id, assigned_at) VALUES (?, ?, ?)",
                    (client_id, int(manager_id), now),
                )

        db.commit()
        return redirect(f"/client/{client_id}")

    # GET: Fetch client data and managers for the form
    client = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not client:
        abort(404)

    # Get all account managers
    managers = db.execute(
        "SELECT * FROM account_managers WHERE state = 1 ORDER BY name"
    ).fetchall()

    # Get currently assigned managers
    assigned_manager_ids = db.execute(
        "SELECT manager_id FROM client_account_managers WHERE client_id = ?",
        (client_id,),
    ).fetchall()
    assigned_manager_ids = [m["manager_id"] for m in assigned_manager_ids]

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
    db = get_db()
    db.execute("DELETE FROM vm_clients WHERE client_id = ?", (client_id,))
    db.execute("DELETE FROM client_contacts WHERE client_id = ?", (client_id,))
    db.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    db.commit()
    return redirect("/clients")


@bp.route("/client/<int:client_id>/contact/add", methods=["POST"])
@login_required
def add_client_contact(client_id):
    """Add a contact for a client."""
    db = get_db()

    # Verify client exists
    client = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not client:
        abort(404)

    # Get form data
    name = request.form.get("name")
    job_title = request.form.get("job_title")
    email = request.form.get("email")
    phone = request.form.get("phone")
    mobile_phone = request.form.get("mobile_phone")
    is_primary_contact = 1 if request.form.get("is_primary_contact") else 0

    # Validate required fields
    if not name:
        flash("Name is required", "error")
        return redirect(f"/client/{client_id}")

    # If this is the primary contact, uncheck all other contacts for this client
    if is_primary_contact:
        db.execute(
            "UPDATE client_contacts SET is_primary_contact = 0 WHERE client_id = ?",
            (client_id,),
        )

    # Insert the new contact
    db.execute(
        """
        INSERT INTO client_contacts (
            client_id, name, job_title, email, phone, mobile_phone, is_primary_contact
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (client_id, name, job_title, email, phone, mobile_phone, is_primary_contact),
    )

    db.commit()
    flash("Contact added successfully", "success")
    return redirect(f"/client/{client_id}")


@bp.route("/vm/<vm_id>/assign-client", methods=["POST"])
@login_required
def assign_vm_client(vm_id):
    """Assign a VM to a client."""
    client_id = request.form.get("client_id")
    db = get_db()

    # Remove existing assignment
    db.execute("DELETE FROM vm_clients WHERE vm_id = ?", (vm_id,))

    if client_id:
        db.execute(
            "INSERT INTO vm_clients (vm_id, client_id) VALUES (?, ?)",
            (vm_id, client_id),
        )

    db.commit()
    return redirect(f"/vm/{vm_id}")


@bp.route("/client/<int:client_id>/notes/add", methods=["POST"])
@login_required
def add_client_note(client_id):
    """Add a new note to a client."""
    note_text = request.form.get("note_text", "").strip()
    db = get_db()
    cursor = db.cursor()

    client = db.execute("SELECT id FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not client:
        return jsonify({"success": False, "error": "Client not found"}), 404

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO client_notes (client_id, note_text, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (client_id, note_text, now, now),
    )
    db.commit()

    return jsonify({"success": True, "note_id": cursor.lastrowid})


@bp.route("/client/<int:client_id>/notes/<int:note_id>/edit", methods=["POST"])
@login_required
def edit_client_note(client_id, note_id):
    """Edit an existing client note."""
    note_text = request.form.get("note_text", "").strip()
    db = get_db()
    cursor = db.cursor()

    note = db.execute(
        "SELECT * FROM client_notes WHERE id = ? AND client_id = ?",
        (note_id, client_id),
    ).fetchone()
    if not note:
        return jsonify({"success": False, "error": "Note not found"}), 404

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "UPDATE client_notes SET note_text = ?, updated_at = ? WHERE id = ?",
        (note_text, now, note_id),
    )
    db.commit()

    return jsonify({"success": True})


@bp.route("/client/<int:client_id>/notes/<int:note_id>/delete", methods=["POST"])
@login_required
def delete_client_note(client_id, note_id):
    """Delete a client note."""
    db = get_db()

    note = db.execute(
        "SELECT * FROM client_notes WHERE id = ? AND client_id = ?",
        (note_id, client_id),
    ).fetchone()
    if not note:
        return jsonify({"success": False, "error": "Note not found"}), 404

    db.execute("DELETE FROM client_notes WHERE id = ?", (note_id,))
    db.commit()

    return jsonify({"success": True})


@bp.route("/client/<int:client_id>/notes")
@login_required
def get_client_notes(client_id):
    """Get all notes for a client."""
    db = get_db()
    notes = db.execute(
        "SELECT id, note_text, created_at, updated_at FROM client_notes WHERE client_id = ? ORDER BY created_at DESC",
        (client_id,),
    ).fetchall()

    return jsonify(
        [
            {"id": n[0], "note_text": n[1], "created_at": n[2], "updated_at": n[3]}
            for n in notes
        ]
    )
