# Hyper-V Parallel Sync Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all 16 bugs preventing the parallel Hyper-V chord/group sync from working.

**Architecture:** Fix in-place — add missing DB columns via ALTER TABLE migrations, fix cluster route persistence, add `@shared_task` decorators, add `dnspython` DNS resolution helper, and flatten the chord group into a single task list.

**Tech Stack:** Flask, SQLite (sqlite3), Celery 5, Redis, pywinrm, dnspython, cryptography (Fernet)

---

## Task 1: Add missing dependencies

**Files:**
- Modify: `requirements.txt`

**Step 1: Update requirements.txt**

```text
# Flask and extensions
Flask>=3.0.0
bootstrap-flask>=2.2.0
python-dotenv>=1.0.0

# Celery for async tasks
celery>=5.3.0
redis>=5.0.0

# WinRM for Hyper-V communication
pywinrm>=0.4.3

# DNS resolution for cluster nodes
dnspython>=2.4.0

# Password encryption
cryptography>=41.0.0

# Production server
gunicorn>=21.0.0
```

**Step 2: Install**

```bash
cd /opt/hyperv_inventory && source venv/bin/activate && pip install dnspython cryptography
```

Expected: both packages install without errors.

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "feat: add dnspython and cryptography dependencies"
```

---

## Task 2: Fix database schema migrations

**Files:**
- Modify: `app/utils/db.py`

**Step 1: Write a migration test**

Create `tests/test_db_migrations.py`:

```python
"""Test that DB migrations run cleanly on existing schemas."""
import sqlite3
import pytest
import sys, os
sys.path.insert(0, "/opt/hyperv_inventory")


def make_db():
    """Create an in-memory DB with the OLD schema (no new columns)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_name TEXT NOT NULL,
            location TEXT,
            is_enabled INTEGER DEFAULT 1
        );
        CREATE TABLE hyperv_hosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_name TEXT UNIQUE,
            cluster_name TEXT,
            total_memory_gb REAL,
            available_memory_gb REAL,
            logical_processors INTEGER,
            vm_count INTEGER,
            os_version TEXT,
            last_updated TEXT
        );
        CREATE TABLE cluster_shared_volumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            volume_path TEXT,
            last_updated TEXT,
            UNIQUE(name, volume_path)
        );
        CREATE TABLE csv_scan_metadata (
            id INTEGER PRIMARY KEY,
            last_scan_start TEXT,
            scan_status TEXT
        );
        CREATE TABLE sync_metadata (id INTEGER PRIMARY KEY);
    """)
    return conn


def test_clusters_migration_adds_all_columns(monkeypatch):
    """Running migrations adds all WinRM columns to clusters table."""
    from app.utils import db as dbmod
    conn = make_db()
    monkeypatch.setattr(dbmod, "get_db_connection", lambda: conn)

    dbmod._run_migrations(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(clusters)").fetchall()}
    for col in ("domain", "cluster_name_for_ps", "domain_name", "dns_servers",
                "username", "password", "transport", "require_https"):
        assert col in cols, f"Missing column: {col}"


def test_cluster_shared_volumes_gets_cluster_name(monkeypatch):
    """Migration adds cluster_name column and fixes UNIQUE constraint."""
    from app.utils import db as dbmod
    conn = make_db()
    monkeypatch.setattr(dbmod, "get_db_connection", lambda: conn)

    dbmod._run_migrations(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(cluster_shared_volumes)").fetchall()}
    assert "cluster_name" in cols


def test_migrations_are_idempotent(monkeypatch):
    """Running migrations twice does not raise."""
    from app.utils import db as dbmod
    conn = make_db()
    monkeypatch.setattr(dbmod, "get_db_connection", lambda: conn)

    dbmod._run_migrations(conn)
    dbmod._run_migrations(conn)  # second run must not raise
```

**Step 2: Run test to verify it fails**

```bash
cd /opt/hyperv_inventory && source venv/bin/activate
mkdir -p tests && touch tests/__init__.py
pytest tests/test_db_migrations.py -v 2>&1 | head -30
```

Expected: FAIL — `_run_migrations` does not exist yet.

**Step 3: Add `_run_migrations` and call it from `init_db` in `app/utils/db.py`**

In `app/utils/db.py`, add this function BEFORE `init_db()` and call it at the end of `init_db()`:

```python
def _run_migrations(db):
    """Run schema migrations for existing databases. Safe to call multiple times."""

    # --- clusters: add WinRM connection columns ---
    _add_column(db, "clusters", "domain", "TEXT")
    _add_column(db, "clusters", "cluster_name_for_ps", "TEXT")
    _add_column(db, "clusters", "domain_name", "TEXT")
    _add_column(db, "clusters", "dns_servers", "TEXT")
    _add_column(db, "clusters", "username", "TEXT")
    _add_column(db, "clusters", "password", "TEXT")
    _add_column(db, "clusters", "transport", "TEXT DEFAULT 'ntlm'")
    _add_column(db, "clusters", "require_https", "INTEGER DEFAULT 0")

    # --- csv_scan_metadata: add cluster_id ---
    _add_column(db, "csv_scan_metadata", "cluster_id", "INTEGER")

    # --- sync_metadata: add missing columns ---
    _add_column(db, "sync_metadata", "hosts_discovered", "INTEGER")
    _add_column(db, "sync_metadata", "hosts_processed", "INTEGER")
    _add_column(db, "sync_metadata", "current_host", "TEXT")

    # --- cluster_shared_volumes: add cluster_name + fix UNIQUE constraint ---
    _migrate_cluster_shared_volumes(db)

    db.commit()
    logger.info("Schema migrations complete")


def _add_column(db, table, column, col_type):
    """Add a column to a table if it does not already exist."""
    try:
        existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            logger.info(f"Added column {table}.{column}")
    except Exception as e:
        logger.warning(f"Could not add {table}.{column}: {e}")


def _migrate_cluster_shared_volumes(db):
    """Recreate cluster_shared_volumes with cluster_name in the UNIQUE constraint."""
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(cluster_shared_volumes)").fetchall()}
        if "cluster_name" in cols:
            return  # already migrated

        db.execute("ALTER TABLE cluster_shared_volumes RENAME TO _csv_backup")
        db.execute("""
            CREATE TABLE cluster_shared_volumes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                volume_path TEXT,
                owner_node TEXT,
                state TEXT,
                total_size_gb REAL,
                free_space_gb REAL,
                used_space_gb REAL,
                percent_used REAL,
                maintenance_mode INTEGER DEFAULT 0,
                redirected_access INTEGER DEFAULT 0,
                vhd_count INTEGER DEFAULT 0,
                vhd_max_size_gb REAL DEFAULT 0,
                vhd_actual_size_gb REAL DEFAULT 0,
                oversubscription_percent REAL DEFAULT 0,
                oversubscription_gb REAL DEFAULT 0,
                cluster_name TEXT,
                last_updated TEXT,
                UNIQUE(name, volume_path, cluster_name)
            )
        """)
        db.execute("DROP TABLE _csv_backup")
        logger.info("Migrated cluster_shared_volumes: added cluster_name to UNIQUE constraint")
    except Exception as e:
        logger.warning(f"cluster_shared_volumes migration: {e}")
```

At the bottom of `init_db()`, add before `db.commit()`:

```python
    _run_migrations(db)
```

Also update the `CREATE TABLE IF NOT EXISTS cluster_shared_volumes` in the `executescript` block to include `cluster_name` and the correct UNIQUE constraint so new installs get it right from the start:

```sql
        -- Cluster Shared Volumes table
        CREATE TABLE IF NOT EXISTS cluster_shared_volumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            volume_path TEXT,
            owner_node TEXT,
            state TEXT,
            total_size_gb REAL,
            free_space_gb REAL,
            used_space_gb REAL,
            percent_used REAL,
            maintenance_mode INTEGER DEFAULT 0,
            redirected_access INTEGER DEFAULT 0,
            vhd_count INTEGER DEFAULT 0,
            vhd_max_size_gb REAL DEFAULT 0,
            vhd_actual_size_gb REAL DEFAULT 0,
            oversubscription_percent REAL DEFAULT 0,
            oversubscription_gb REAL DEFAULT 0,
            cluster_name TEXT,
            last_updated TEXT,
            UNIQUE(name, volume_path, cluster_name)
        );
```

Also update `csv_scan_metadata` in the `executescript` to include `cluster_id`:

```sql
        -- CSV scan metadata table
        CREATE TABLE IF NOT EXISTS csv_scan_metadata (
            id INTEGER PRIMARY KEY,
            cluster_id INTEGER,
            last_scan_start TEXT,
            last_scan_end TEXT,
            scan_status TEXT,
            volumes_scanned INTEGER,
            total_vhds_found INTEGER,
            scan_duration_seconds REAL,
            errors TEXT
        );
```

Remove the duplicate `CREATE TABLE IF NOT EXISTS cluster_shared_volumes` and `CREATE TABLE IF NOT EXISTS csv_scan_metadata` blocks further down in `init_db()` (there are standalone try/except blocks that create these — delete them since `executescript` handles it now).

**Step 4: Fix `logical_processor_count` → `logical_processors` in `save_host_to_db`**

In `tasks/sync.py`, find `save_host_to_db` and fix the INSERT column name:

```python
    cursor.execute(
        """
        INSERT OR REPLACE INTO hyperv_hosts (
            host_name, cluster_name, total_memory_gb, available_memory_gb,
            logical_processors, vm_count, os_version, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
```

**Step 5: Run tests**

```bash
pytest tests/test_db_migrations.py -v
```

Expected: all 3 tests PASS.

**Step 6: Commit**

```bash
git add app/utils/db.py tasks/sync.py tests/
git commit -m "fix: add schema migrations for all missing columns"
```

---

## Task 3: Fix cluster routes — save and load all WinRM fields

**Files:**
- Modify: `app/routes/clusters.py`

**Step 1: Replace the entire file**

The file needs: a `_encrypt_password` helper, fixed `add_cluster`, fixed `edit_cluster`, fixed SELECT queries in `cluster_list`/`cluster_details`, and a real `test_cluster_connection`.

Full replacement of `app/routes/clusters.py`:

```python
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
```

**Step 2: Commit**

```bash
git add app/routes/clusters.py
git commit -m "fix: save and load all WinRM fields in cluster routes"
```

---

## Task 4: Fix DNS resolution and `discover_cluster_nodes`

**Files:**
- Modify: `tasks/sync.py`
- Create: `tests/test_dns_resolution.py`

**Step 1: Write the failing test**

Create `tests/test_dns_resolution.py`:

```python
"""Test DNS resolution helper for cluster nodes."""
import pytest
import sys
sys.path.insert(0, "/opt/hyperv_inventory")


def test_resolve_uses_custom_dns_servers(mocker):
    """resolve_node_ip uses the configured DNS servers, not system DNS."""
    mock_resolver_class = mocker.patch("dns.resolver.Resolver")
    mock_resolver = mock_resolver_class.return_value
    mock_answer = mocker.MagicMock()
    mock_answer.__str__ = lambda self: "10.21.0.15"
    mock_resolver.resolve.return_value = [mock_answer]

    from tasks.sync import resolve_node_ip

    ip = resolve_node_ip("NODE1", "10.21.0.10,10.21.0.11", "egitdr.corp")

    # Must configure resolver with our DNS servers (not system)
    assert mock_resolver.nameservers == ["10.21.0.10", "10.21.0.11"]
    # Must try FQDN first
    mock_resolver.resolve.assert_called_with("NODE1.egitdr.corp", "A")


def test_resolve_falls_back_to_short_name(mocker):
    """Falls back to short name if FQDN resolution fails."""
    mock_resolver_class = mocker.patch("dns.resolver.Resolver")
    mock_resolver = mock_resolver_class.return_value
    mock_answer = mocker.MagicMock()
    mock_answer.__str__ = lambda self: "10.21.0.15"

    # FQDN fails, short name succeeds
    mock_resolver.resolve.side_effect = [Exception("NXDOMAIN"), [mock_answer]]

    from tasks.sync import resolve_node_ip

    ip = resolve_node_ip("NODE1", "10.21.0.10", "egitdr.corp")
    assert mock_resolver.resolve.call_count == 2


def test_resolve_returns_hostname_when_all_fail(mocker):
    """Returns the node name as-is when all DNS queries fail."""
    mock_resolver_class = mocker.patch("dns.resolver.Resolver")
    mock_resolver = mock_resolver_class.return_value
    mock_resolver.resolve.side_effect = Exception("timeout")

    from tasks.sync import resolve_node_ip

    result = resolve_node_ip("NODE1", "10.21.0.10", None)
    assert result == "NODE1"


def test_resolve_uses_system_dns_when_no_servers(mocker):
    """Uses socket.gethostbyname when no custom DNS servers are configured."""
    mock_gethostbyname = mocker.patch("socket.gethostbyname", return_value="10.21.0.20")

    from tasks.sync import resolve_node_ip

    result = resolve_node_ip("NODE1", "", None)
    mock_gethostbyname.assert_called_once_with("NODE1")
    assert result == "10.21.0.20"
```

**Step 2: Run test to verify it fails**

```bash
pip install pytest-mock
pytest tests/test_dns_resolution.py -v 2>&1 | head -20
```

Expected: FAIL — `resolve_node_ip` does not exist yet.

**Step 3: Add `resolve_node_ip` and rewrite `discover_cluster_nodes` in `tasks/sync.py`**

Add `resolve_node_ip` after the imports section (before the PowerShell scripts):

```python
def resolve_node_ip(node_name: str, dns_servers_str: str, domain_name: str = None) -> str:
    """Resolve a cluster node name to an IP using configured DNS servers.

    Args:
        node_name: Short node name from Get-ClusterNode (e.g. 'NODE1')
        dns_servers_str: Comma-separated DNS server IPs from cluster config
        domain_name: Domain suffix to build FQDN (e.g. 'egitdr.corp')

    Returns:
        Resolved IP address, or node_name if resolution fails
    """
    import dns.resolver

    if not dns_servers_str or not dns_servers_str.strip():
        # No custom DNS — fall back to system resolver
        try:
            return socket.gethostbyname(node_name)
        except socket.gaierror:
            logger.warning(f"System DNS could not resolve {node_name}")
            return node_name

    dns_servers = [s.strip() for s in dns_servers_str.split(",") if s.strip()]
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = dns_servers
    resolver.timeout = 3
    resolver.lifetime = 5

    # Try FQDN first when domain_name is configured
    if domain_name:
        fqdn = f"{node_name}.{domain_name}"
        try:
            answer = resolver.resolve(fqdn, "A")
            ip = str(answer[0])
            logger.info(f"Resolved {fqdn} -> {ip}")
            return ip
        except Exception:
            pass  # fall through to short name

    # Try short name
    try:
        answer = resolver.resolve(node_name, "A")
        ip = str(answer[0])
        logger.info(f"Resolved {node_name} -> {ip}")
        return ip
    except Exception:
        logger.warning(f"Could not resolve {node_name} via custom DNS, using as-is")
        return node_name
```

Replace `discover_cluster_nodes` entirely:

```python
def discover_cluster_nodes(cluster_name: str, cluster_id: int = None) -> Dict[str, str]:
    """Discover all Up nodes in a Hyper-V cluster and resolve their IPs.

    Connects directly to the cluster FQDN (domain field) to run Get-ClusterNode,
    then resolves each node name to an IP using the cluster's configured DNS servers.

    Returns:
        Dict mapping node_name -> ip_address
    """
    logger.info(f"Discovering nodes for cluster: {cluster_name}")

    domain = cluster_name  # fallback if no DB record
    dns_servers = None
    domain_name = None

    if cluster_id:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT domain, dns_servers, domain_name FROM clusters WHERE id = ?",
                (cluster_id,),
            )
            result = cursor.fetchone()
            conn.close()
            if result:
                domain = result[0] or cluster_name
                dns_servers = result[1]
                domain_name = result[2]
                logger.info(
                    f"Cluster config: domain={domain}, dns_servers={dns_servers}, "
                    f"domain_name={domain_name}"
                )
        except Exception as e:
            logger.warning(f"Failed to get cluster config from DB: {e}")

    # Connect directly to the cluster FQDN
    logger.info(f"Connecting to cluster FQDN: {domain}")
    session = create_winrm_session(domain, cluster_id=cluster_id)

    # Query cluster nodes
    nodes_info = run_powershell(session, PS_GET_CLUSTER_NODES)
    if not nodes_info:
        raise Exception(f"Get-ClusterNode returned no results from {domain}")

    node_map = {}
    for node in nodes_info:
        state = node.get("State", "")
        node_name = node.get("Name", "")
        if state != "Up":
            logger.info(f"Skipping node {node_name} — state={state}")
            continue
        ip = resolve_node_ip(node_name, dns_servers, domain_name)
        node_map[node_name] = ip
        logger.info(f"Node {node_name} -> {ip}")

    if not node_map:
        raise Exception(f"No Up nodes found in cluster {cluster_name}")

    logger.info(f"Discovered {len(node_map)} node(s): {list(node_map.keys())}")
    return node_map
```

**Step 4: Run tests**

```bash
pytest tests/test_dns_resolution.py -v
```

Expected: all 4 tests PASS.

**Step 5: Commit**

```bash
git add tasks/sync.py tests/test_dns_resolution.py
git commit -m "fix: add resolve_node_ip helper and rewrite discover_cluster_nodes to use cluster domain field"
```

---

## Task 5: Fix the PowerShell syntax error in `PS_GET_VMS`

**Files:**
- Modify: `tasks/sync.py`

**Step 1: Find and fix the broken line**

In `tasks/sync.py`, inside `PS_GET_VMS`, line 58 reads:

```powershell
        VirtualHardDiskPath = $vm.VirtualHardDisks[0].Path if ($vm.VirtualHardDisks.Count -gt 0) { $null }
```

Replace with valid PowerShell `if/else`:

```powershell
        VirtualHardDiskPath = if ($vm.VirtualHardDisks.Count -gt 0) { $vm.VirtualHardDisks[0].Path } else { $null }
```

**Step 2: Commit**

```bash
git add tasks/sync.py
git commit -m "fix: correct PowerShell syntax error in PS_GET_VMS VirtualHardDiskPath field"
```

---

## Task 6: Fix Celery task decorators and flatten the chord group

**Files:**
- Modify: `tasks/sync.py`

**Step 1: Add `@shared_task` to `aggregate_sync_results_with_csv`**

Find the function definition:

```python
def aggregate_sync_results_with_csv(results):
```

Replace with:

```python
@shared_task(name="tasks.sync.aggregate_sync_results_with_csv")
def aggregate_sync_results_with_csv(results):
```

**Step 2: Rewrite `fetch_hyperv_data` to use flat group**

Replace the entire `fetch_hyperv_data` function:

```python
@shared_task(bind=True, name="tasks.sync.fetch_hyperv_data")
def fetch_hyperv_data(self):
    """Orchestrate parallel Hyper-V sync: discover nodes, dispatch worker tasks."""
    logger.info("Starting Hyper-V data collection (parallel chord mode)")

    # Load all enabled clusters from database
    conn = get_db_connection()
    cursor = conn.cursor()
    clusters = cursor.execute(
        "SELECT id, cluster_name, domain FROM clusters WHERE is_enabled = 1"
    ).fetchall()
    conn.close()

    if not clusters:
        msg = "No enabled clusters found in database"
        logger.error(msg)
        update_sync_metadata("error", errors=msg, hosts_discovered=0)
        return {"status": "error", "message": msg}

    logger.info(f"Found {len(clusters)} enabled cluster(s)")

    all_task_signatures = []
    cluster_info = []          # (cluster_id, cluster_name, [node_ips])
    node_to_cluster = {}       # ip -> cluster_id
    total_nodes = 0

    for row in clusters:
        cluster_id, cluster_name, domain = row[0], row[1], row[2] or row[1]
        logger.info(f"Discovering nodes for: {cluster_name}")

        try:
            node_map = discover_cluster_nodes(cluster_name, cluster_id=cluster_id)
            # node_map: {node_name: ip}
            node_ips = list(node_map.values())
            cluster_info.append((cluster_id, cluster_name, node_ips))
            total_nodes += len(node_ips)

            for ip in node_ips:
                node_to_cluster[ip] = cluster_id

            logger.info(f"Cluster {cluster_name}: {len(node_ips)} node(s) -> {node_ips}")

        except Exception as e:
            logger.error(f"Node discovery failed for {cluster_name}: {e}")
            # Continue with other clusters

    if total_nodes == 0:
        msg = "No nodes discovered from any cluster"
        logger.error(msg)
        update_sync_metadata("error", errors=msg, hosts_discovered=0)
        return {"status": "error", "message": msg}

    update_sync_metadata("running", start=True, hosts_discovered=total_nodes)
    clear_old_data()

    # Build a FLAT list of all worker tasks
    # Per-node VM sync tasks (one per host IP)
    for ip, cluster_id in node_to_cluster.items():
        all_task_signatures.append(fetch_single_host.s(ip, cluster_id))

    # Per-cluster CSV scan tasks (one per cluster)
    for cluster_id, cluster_name, node_ips in cluster_info:
        all_task_signatures.append(
            fetch_cluster_csv_storage.s(cluster_id, cluster_name, node_ips)
        )

    logger.info(
        f"Dispatching {len(all_task_signatures)} tasks "
        f"({total_nodes} hosts + {len(cluster_info)} CSV scans)"
    )

    try:
        result = chord(group(all_task_signatures))(
            aggregate_sync_results_with_csv.s()
        )
        return {
            "status": "started",
            "message": (
                f"Syncing {total_nodes} node(s) across "
                f"{len(cluster_info)} cluster(s)"
            ),
        }
    except Exception as e:
        logger.error(f"Failed to dispatch parallel sync: {e}")
        update_sync_metadata("error", errors=str(e))
        return {"status": "error", "message": str(e)}
```

**Step 3: Commit**

```bash
git add tasks/sync.py
git commit -m "fix: add @shared_task to aggregate callback and flatten chord group"
```

---

## Task 7: Fix CSV scanner — `@shared_task` and `PS_GET_CSV_INFO`

**Files:**
- Modify: `tasks/csv_scanner.py`

**Step 1: Add `@shared_task` import and decorator**

At the top of `tasks/csv_scanner.py`, add to the imports:

```python
from celery import shared_task
```

Find the function definition:

```python
def fetch_cluster_csv_storage(cluster_id: int, cluster_name: str, nodes: list):
```

Replace with:

```python
@shared_task(name="tasks.csv_scanner.fetch_cluster_csv_storage")
def fetch_cluster_csv_storage(cluster_id: int, cluster_name: str, nodes: list):
```

**Step 2: Fix `PS_GET_CSV_INFO` — remove `$UsingClusterName`**

Find and remove these two lines at the top of the `PS_GET_CSV_INFO` string:

```powershell
# Force using cluster name parameter
$clusterName = $UsingClusterName
```

The full corrected start of `PS_GET_CSV_INFO`:

```python
PS_GET_CSV_INFO = """
# CSV Storage Collection Script
$ErrorActionPreference = 'SilentlyContinue'

$volumes = Get-ClusterSharedVolume
if ($volumes) {
```

(Just delete the two `$clusterName` lines — the rest of the script is correct.)

**Step 3: Register the task in `celeryconfig.py`**

In `celeryconfig.py`, verify the task route key matches the new name. It should already be:

```python
"tasks.csv_scanner.fetch_cluster_csv_storage": {"queue": "csv"},
```

Also remove the broken beat schedule entry for `csv-scan-hourly` (it was calling `fetch_cluster_csv_storage` directly without arguments, which cannot work for a task that requires `cluster_id, cluster_name, nodes`). The CSV scan is now driven by `fetch_hyperv_data`.

Replace `celeryconfig.py` content:

```python
# Celery Beat Schedule Configuration

from celery import Celery
from celery.schedules import crontab

celery = Celery("tasks")

celery.conf.update(
    broker_url="redis://localhost:6379/0",
    result_backend="redis://localhost:6379/0",
    task_routes={
        "tasks.sync.fetch_hyperv_data": {"queue": "hyperv"},
        "tasks.sync.fetch_single_host": {"queue": "hyperv"},
        "tasks.sync.aggregate_sync_results_with_csv": {"queue": "hyperv"},
        "tasks.csv_scanner.fetch_cluster_csv_storage": {"queue": "csv"},
    },
)

beat_schedule = {
    # Full sync (VMs + CSV) daily at midnight
    "full-sync-daily-midnight": {
        "task": "tasks.sync.fetch_hyperv_data",
        "schedule": crontab(hour=0, minute=0),
        "options": {"expires": 86400},
    },
}
```

**Step 4: Commit**

```bash
git add tasks/csv_scanner.py celeryconfig.py
git commit -m "fix: add @shared_task to CSV scanner, fix PS_GET_CSV_INFO, remove bad beat schedule"
```

---

## Task 8: Smoke test the full setup

**Step 1: Verify the app still starts**

```bash
cd /opt/hyperv_inventory && source venv/bin/activate
python -c "from app import create_app; app = create_app(); print('App OK')"
```

Expected: `App OK` — no import errors.

**Step 2: Verify Celery task imports**

```bash
python -c "
from tasks.sync import fetch_hyperv_data, fetch_single_host, aggregate_sync_results_with_csv
from tasks.csv_scanner import fetch_cluster_csv_storage
print('fetch_hyperv_data is task:', hasattr(fetch_hyperv_data, 'delay'))
print('aggregate is task:', hasattr(aggregate_sync_results_with_csv, 'delay'))
print('fetch_cluster_csv_storage is task:', hasattr(fetch_cluster_csv_storage, 'delay'))
"
```

Expected:
```
fetch_hyperv_data is task: True
aggregate is task: True
fetch_cluster_csv_storage is task: True
```

**Step 3: Run all tests**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

**Step 4: Final commit**

```bash
git add -A
git commit -m "fix: complete parallel sync fix — all 16 bugs resolved"
```
