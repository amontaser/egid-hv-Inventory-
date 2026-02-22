# Hyper-V Inventory Full Rewrite — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rewrite the Hyper-V inventory system with clean layer separation, working change detection, physical disk collection, and in-app/email/webhook notifications.

**Architecture:** Three-layer design — collectors (WinRM/PowerShell → dicts), persistence (dicts → SQLite), monitor (old snapshot vs new data → notifications). Orchestrator Celery task wires all three layers together via chord.

**Tech Stack:** Flask 3.x, Celery 5.x, Redis, SQLite (sqlite3), pywinrm, Fernet encryption, smtplib, requests

**Design doc:** `docs/plans/2026-02-22-hyperv-inventory-rewrite-design.md`

---

## Overview of changes

| Old file | Fate |
|----------|------|
| `tasks/hyperv.py` | Replaced by `tasks/collectors/winrm.py` |
| `tasks/sync.py` | Replaced by `tasks/orchestrator.py` |
| `tasks/csv_scanner.py` | Replaced by `tasks/collectors/storage.py` + `tasks/persistence/storage.py` |
| `app/utils/db.py` | Kept as re-export shim; logic moves to `app/db/` |
| `app/routes/settings.py` | Extended with notification settings UI |

**Files not changed:** all templates, auth.py, clients.py, vms.py, hosts.py (route logic), storage.py (route logic), clusters.py, notifications.py, celeryconfig.py, gunicorn.conf.py

---

## Task 1: Create DB layer (`app/db/`)

**Files:**
- Create: `app/db/__init__.py`
- Create: `app/db/migrations.py`
- Modify: `app/utils/db.py` (make it a re-export shim)

**What this does:** Move all DB initialization + migration logic to `app/db/`. Add two new tables: `host_physical_disks` and `settings`. Keep `app/utils/db.py` as a one-line re-export so existing routes don't need to change.

**Step 1: Create `app/db/__init__.py`**

```python
"""Database layer — connection factory, schema init, migrations."""

import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime

from .migrations import run_migrations

logger = logging.getLogger(__name__)

DATABASE_PATH = "/opt/hyperv_inventory/database.db"


def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db():
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        conn.close()


def init_db():
    """Initialize all tables and run migrations. Safe to call multiple times."""
    db = get_db_connection()
    _create_tables(db)
    run_migrations(db)
    _seed_defaults(db)
    db.commit()
    db.close()
    logger.info("Database initialized successfully")


def _create_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS vm_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_name TEXT NOT NULL,
            vm_id TEXT UNIQUE,
            cluster_name TEXT,
            host_name TEXT,
            state TEXT,
            uptime_seconds INTEGER,
            cpu_count INTEGER,
            memory_assigned_gb REAL,
            memory_demand_gb REAL,
            memory_startup_gb REAL,
            memory_minimum_gb REAL,
            memory_maximum_gb REAL,
            dynamic_memory_enabled INTEGER,
            generation INTEGER,
            version TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS vm_disks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_id TEXT NOT NULL,
            disk_name TEXT,
            disk_path TEXT,
            disk_format TEXT,
            size_gb REAL,
            used_gb REAL,
            controller_type TEXT,
            controller_number INTEGER,
            controller_location INTEGER,
            FOREIGN KEY (vm_id) REFERENCES vm_info(vm_id)
        );

        CREATE TABLE IF NOT EXISTS vm_network_adapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_id TEXT NOT NULL,
            adapter_name TEXT,
            switch_name TEXT,
            vlan_id INTEGER,
            mac_address TEXT,
            ip_addresses TEXT,
            is_connected INTEGER,
            bandwidth_setting TEXT,
            FOREIGN KEY (vm_id) REFERENCES vm_info(vm_id)
        );

        CREATE TABLE IF NOT EXISTS vm_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_id TEXT NOT NULL,
            snapshot_name TEXT,
            snapshot_id TEXT,
            snapshot_type TEXT,
            creation_time TEXT,
            parent_snapshot_id TEXT,
            FOREIGN KEY (vm_id) REFERENCES vm_info(vm_id)
        );

        CREATE TABLE IF NOT EXISTS vm_replication (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_id TEXT NOT NULL,
            replication_state TEXT,
            replication_health TEXT,
            replication_mode TEXT,
            primary_server TEXT,
            replica_server TEXT,
            frequency_seconds INTEGER,
            last_replication_time TEXT,
            FOREIGN KEY (vm_id) REFERENCES vm_info(vm_id)
        );

        CREATE TABLE IF NOT EXISTS hyperv_hosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_name TEXT UNIQUE,
            cluster_name TEXT,
            total_memory_gb REAL,
            available_memory_gb REAL,
            logical_processors INTEGER,
            vm_count INTEGER,
            os_version TEXT,
            hyperv_version TEXT,
            vhd_default_path TEXT,
            vm_default_path TEXT,
            connection_ip TEXT,
            last_updated TEXT
        );

        CREATE TABLE IF NOT EXISTS host_physical_disks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_name TEXT NOT NULL,
            friendly_name TEXT,
            serial_number TEXT,
            media_type TEXT,
            size_gb REAL,
            health_status TEXT,
            operational_status TEXT,
            bus_type TEXT,
            partition_style TEXT,
            disk_number INTEGER,
            last_updated TEXT,
            UNIQUE(host_name, serial_number)
        );

        CREATE TABLE IF NOT EXISTS sync_metadata (
            id INTEGER PRIMARY KEY,
            last_sync_start TEXT,
            last_sync_end TEXT,
            last_sync_status TEXT,
            vms_synced INTEGER,
            hosts_discovered INTEGER,
            hosts_processed INTEGER,
            current_host TEXT,
            current_cluster TEXT,
            errors TEXT
        );

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

        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            website TEXT,
            country TEXT,
            description TEXT,
            state INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS client_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            is_primary_contact INTEGER DEFAULT 0,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        );

        CREATE TABLE IF NOT EXISTS vm_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_id TEXT NOT NULL,
            client_id INTEGER NOT NULL,
            FOREIGN KEY (vm_id) REFERENCES vm_info(vm_id),
            FOREIGN KEY (client_id) REFERENCES clients(id)
        );

        CREATE TABLE IF NOT EXISTS vm_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_id TEXT NOT NULL,
            machine_name TEXT NOT NULL,
            change_type TEXT NOT NULL,
            field_name TEXT,
            old_value TEXT,
            new_value TEXT,
            change_description TEXT,
            detected_at TEXT NOT NULL,
            FOREIGN KEY (vm_id) REFERENCES vm_info(vm_id)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_type TEXT NOT NULL,
            vm_id TEXT,
            machine_name TEXT NOT NULL,
            message TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            cluster_name TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS account_managers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            state INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS client_account_managers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            manager_id INTEGER NOT NULL,
            assigned_at TEXT,
            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
            FOREIGN KEY (manager_id) REFERENCES account_managers(id) ON DELETE CASCADE,
            UNIQUE(client_id, manager_id)
        );

        CREATE TABLE IF NOT EXISTS vm_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_id TEXT NOT NULL,
            note_text TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (vm_id) REFERENCES vm_info(vm_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS client_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            note_text TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_name TEXT NOT NULL UNIQUE,
            location TEXT,
            is_enabled INTEGER DEFAULT 1,
            domain TEXT,
            cluster_name_for_ps TEXT,
            domain_name TEXT,
            dns_servers TEXT,
            username TEXT,
            password TEXT,
            transport TEXT DEFAULT 'ntlm',
            require_https INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_vm_info_machine_name ON vm_info(machine_name);
        CREATE INDEX IF NOT EXISTS idx_vm_info_host ON vm_info(host_name);
        CREATE INDEX IF NOT EXISTS idx_vm_info_state ON vm_info(state);
        CREATE INDEX IF NOT EXISTS idx_vm_disks_vm_id ON vm_disks(vm_id);
        CREATE INDEX IF NOT EXISTS idx_vm_network_vm_id ON vm_network_adapters(vm_id);
        CREATE INDEX IF NOT EXISTS idx_vm_snapshots_vm_id ON vm_snapshots(vm_id);
        CREATE INDEX IF NOT EXISTS idx_vm_history_vm_id ON vm_history(vm_id);
        CREATE INDEX IF NOT EXISTS idx_vm_history_date ON vm_history(detected_at);
        CREATE INDEX IF NOT EXISTS idx_vm_history_type ON vm_history(change_type);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_vm_history_one_time_events
            ON vm_history(vm_id, change_type)
            WHERE change_type IN ('discovered', 'created', 'deleted');
        CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(is_read);
        CREATE INDEX IF NOT EXISTS idx_notifications_date ON notifications(created_at);
        CREATE INDEX IF NOT EXISTS idx_host_disks_host ON host_physical_disks(host_name);
    """)


def _seed_defaults(db):
    try:
        count = db.execute("SELECT COUNT(*) FROM account_managers").fetchone()[0]
        if count == 0:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.execute("""
                INSERT INTO account_managers (name, email, phone, state, created_at, updated_at)
                VALUES
                  ('Ahmed Mohamed', 'ahmed.mohamed@company.com', '+1-555-0101', 1, ?, ?),
                  ('Sara Ali', 'sara.ali@company.com', '+1-555-0102', 1, ?, ?),
                  ('Omar Hassan', 'omar.hassan@company.com', '+1-555-0103', 1, ?, ?)
            """, (now, now, now, now, now, now))
    except Exception as e:
        logger.warning(f"Could not seed account managers: {e}")

    # Seed default settings
    defaults = [
        ("storage_threshold_pct", "20"),
        ("enable_email_alerts", "0"),
        ("enable_webhook_alerts", "0"),
        ("webhook_url", ""),
        ("alert_email_to", ""),
    ]
    for key, value in defaults:
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
```

**Step 2: Create `app/db/migrations.py`**

```python
"""Schema migrations — safe to run multiple times."""

import logging

logger = logging.getLogger(__name__)


def run_migrations(db):
    """Run all schema migrations. Each is idempotent."""
    _add_column(db, "clusters", "domain", "TEXT")
    _add_column(db, "clusters", "cluster_name_for_ps", "TEXT")
    _add_column(db, "clusters", "domain_name", "TEXT")
    _add_column(db, "clusters", "dns_servers", "TEXT")
    _add_column(db, "clusters", "username", "TEXT")
    _add_column(db, "clusters", "password", "TEXT")
    _add_column(db, "clusters", "transport", "TEXT DEFAULT 'ntlm'")
    _add_column(db, "clusters", "require_https", "INTEGER DEFAULT 0")
    _add_column(db, "csv_scan_metadata", "cluster_id", "INTEGER")
    _add_column(db, "sync_metadata", "hosts_discovered", "INTEGER")
    _add_column(db, "sync_metadata", "hosts_processed", "INTEGER")
    _add_column(db, "sync_metadata", "current_host", "TEXT")
    _add_column(db, "sync_metadata", "current_cluster", "TEXT")
    _add_column(db, "hyperv_hosts", "connection_ip", "TEXT")
    _add_column(db, "hyperv_hosts", "hyperv_version", "TEXT")
    _add_column(db, "hyperv_hosts", "vhd_default_path", "TEXT")
    _add_column(db, "hyperv_hosts", "vm_default_path", "TEXT")
    _add_column(db, "notifications", "cluster_name", "TEXT")
    _migrate_cluster_shared_volumes(db)
    db.commit()
    logger.info("Migrations complete")


def _add_column(db, table, column, col_type):
    try:
        existing = {r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            logger.info(f"Added column {table}.{column}")
    except Exception as e:
        logger.warning(f"Could not add {table}.{column}: {e}")


def _migrate_cluster_shared_volumes(db):
    """Ensure cluster_name is in the UNIQUE constraint."""
    try:
        backup_exists = db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='_csv_backup'"
        ).fetchone()[0]
        if backup_exists:
            main_exists = db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='cluster_shared_volumes'"
            ).fetchone()[0]
            if main_exists:
                db.execute("DROP TABLE _csv_backup")
            else:
                db.execute("ALTER TABLE _csv_backup RENAME TO cluster_shared_volumes")
            return
        cols = {r[1] for r in db.execute("PRAGMA table_info(cluster_shared_volumes)").fetchall()}
        if "cluster_name" in cols:
            return
        db.execute("ALTER TABLE cluster_shared_volumes RENAME TO _csv_backup")
        db.execute("""
            CREATE TABLE cluster_shared_volumes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, volume_path TEXT, owner_node TEXT, state TEXT,
                total_size_gb REAL, free_space_gb REAL, used_space_gb REAL, percent_used REAL,
                maintenance_mode INTEGER DEFAULT 0, redirected_access INTEGER DEFAULT 0,
                vhd_count INTEGER DEFAULT 0, vhd_max_size_gb REAL DEFAULT 0,
                vhd_actual_size_gb REAL DEFAULT 0, oversubscription_percent REAL DEFAULT 0,
                oversubscription_gb REAL DEFAULT 0, cluster_name TEXT, last_updated TEXT,
                UNIQUE(name, volume_path, cluster_name)
            )
        """)
        db.execute("DROP TABLE _csv_backup")
    except Exception as e:
        logger.warning(f"cluster_shared_volumes migration: {e}")
```

**Step 3: Create `app/db/__init_module__.py`** — actually just create `app/db/__init__.py` as shown above. Also need a blank `app/db/__init__.py` marker — it IS `app/db/__init__.py`.

**Step 4: Update `app/utils/db.py` to re-export from `app/db/`**

Replace the entire file with:
```python
"""Backward-compatible re-export — logic now lives in app/db/."""
from app.db import get_db_connection, get_db, init_db  # noqa: F401
```

**Step 5: Write test**

Create `tests/test_db_layer.py`:
```python
import sqlite3
import pytest
from unittest.mock import patch

# Point DB at a temp file
import app.db as db_module

@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    with patch.object(db_module, "DATABASE_PATH", db_path):
        db_module.init_db()
        yield db_path

def test_init_db_creates_all_tables(tmp_db):
    conn = sqlite3.connect(tmp_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    expected = {
        "vm_info", "vm_disks", "vm_network_adapters", "vm_snapshots",
        "vm_replication", "hyperv_hosts", "host_physical_disks",
        "sync_metadata", "cluster_shared_volumes", "notifications",
        "vm_history", "settings", "clusters",
    }
    assert expected.issubset(tables)

def test_init_db_is_idempotent(tmp_db):
    with patch.object(db_module, "DATABASE_PATH", tmp_db):
        db_module.init_db()  # second call should not raise
        db_module.init_db()

def test_settings_defaults_seeded(tmp_db):
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    keys = {r["key"] for r in conn.execute("SELECT key FROM settings").fetchall()}
    conn.close()
    assert "storage_threshold_pct" in keys
    assert "enable_email_alerts" in keys

def test_get_db_context_manager_commits(tmp_db):
    with patch.object(db_module, "DATABASE_PATH", tmp_db):
        with db_module.get_db() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('test_key', 'val')")
        conn2 = sqlite3.connect(tmp_db)
        row = conn2.execute("SELECT value FROM settings WHERE key='test_key'").fetchone()
        conn2.close()
        assert row[0] == "val"
```

**Step 6: Run tests**

```bash
cd /opt/hyperv_inventory && source venv/bin/activate
pytest tests/test_db_layer.py -v
```
Expected: 4 PASS

**Step 7: Commit**

```bash
git add app/db/ tests/test_db_layer.py app/utils/db.py
git commit -m "feat: add app/db layer with settings + host_physical_disks tables"
```

---

## Task 2: WinRM collector (`tasks/collectors/winrm.py`)

**Files:**
- Create: `tasks/collectors/__init__.py` (empty)
- Create: `tasks/collectors/winrm.py`
- Test: `tests/test_winrm_collector.py`

**Step 1: Create `tasks/collectors/__init__.py`**

Empty file.

**Step 2: Create `tasks/collectors/winrm.py`**

```python
"""WinRM session management and PowerShell execution."""

import os
import base64
import json
import logging
import socket
from typing import Any, Optional

import winrm

logger = logging.getLogger(__name__)


def create_winrm_session(host: str, cluster_id: int = None) -> winrm.Session:
    """Create authenticated WinRM session. Credentials from env or DB (Fernet-encrypted)."""
    username = os.getenv("HYPERV_USERNAME")
    password = os.getenv("HYPERV_PASSWORD")
    transport = "ntlm"

    if (not username or not password) and cluster_id:
        username, password, transport = _load_cluster_credentials(cluster_id)

    if not username or not password:
        raise ValueError(
            "No credentials: set HYPERV_USERNAME/HYPERV_PASSWORD env vars "
            "or configure a cluster with saved credentials."
        )

    return winrm.Session(
        target=host,
        auth=(username, password),
        server_cert_validation="ignore",
        transport=transport,
    )


def _load_cluster_credentials(cluster_id: int):
    """Load and decrypt credentials for a cluster from DB."""
    from app.db import get_db_connection

    conn = get_db_connection()
    row = conn.execute(
        "SELECT username, password, transport, require_https FROM clusters WHERE id = ?",
        (cluster_id,),
    ).fetchone()
    conn.close()

    if not row:
        return None, None, "ntlm"

    username = row["username"]
    encrypted_pw = row["password"]
    transport = "ssl" if row["require_https"] else (row["transport"] or "ntlm")

    password = _decrypt_password(encrypted_pw)
    return username, password, transport


def _decrypt_password(encrypted_pw: str) -> Optional[str]:
    try:
        from cryptography.fernet import Fernet

        key = os.getenv("ENCRYPTION_KEY", "")
        key_file = "/opt/hyperv_inventory/encryption_key.key"
        if not key and os.path.exists(key_file):
            with open(key_file, "rb") as f:
                key = f.read().decode().strip()
        if not key:
            return None
        f = Fernet(key.encode() if isinstance(key, str) else key)
        return f.decrypt(encrypted_pw.encode()).decode()
    except Exception as e:
        logger.error(f"Password decryption failed: {e}")
        return None


def run_ps(session: winrm.Session, script: str) -> Optional[Any]:
    """Run a PowerShell script; return parsed JSON or None on error."""
    try:
        result = session.run_ps(script)
        if result.status_code != 0:
            logger.error(f"PS error: {result.std_err.decode('utf-8', errors='ignore')}")
            return None
        output = result.std_out.decode("utf-8", errors="ignore").strip()
        if not output or output in ("null", "[]"):
            return []
        data = json.loads(output)
        return [data] if isinstance(data, dict) else data
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return None
    except OSError:
        raise  # propagate connection failures to caller
    except Exception as e:
        logger.error(f"PS execution error: {e}")
        return None


def run_ps_long(session: winrm.Session, script: str) -> Optional[Any]:
    """Run large PS scripts via Base64 encoding to bypass WinRM size limits.
    Falls back to run_ps for scripts under 7000 bytes."""
    script_bytes = script.encode("utf-8")
    if len(script_bytes) < 7000:
        return run_ps(session, script)

    encoded = base64.b64encode(script_bytes).decode("ascii")
    wrapper = f"""
        $ErrorActionPreference = "Stop"
        try {{
            $bytes = [System.Convert]::FromBase64String('{encoded}')
            $script = [System.Text.Encoding]::UTF8.GetString($bytes)
            Invoke-Expression -Command $script
        }} catch {{
            Write-Error $_.Exception.Message
            exit 1
        }}
    """
    result = session.run_ps(wrapper)
    if result.status_code != 0:
        logger.error(f"PS Base64 error: {result.std_err.decode('utf-8', errors='ignore')}")
        return None
    output = result.std_out.decode("utf-8", errors="ignore").strip()
    if not output or output in ("null", "[]"):
        return []
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error (Base64 path): {e}")
        return None


def resolve_node_ip(node_name: str, dns_servers_str: str, domain_name: str = None) -> str:
    """Resolve a cluster node name to IP using configured DNS servers."""
    import dns.resolver

    if not dns_servers_str or not dns_servers_str.strip():
        try:
            return socket.gethostbyname(node_name)
        except socket.gaierror:
            pass
        if domain_name:
            fqdn = f"{node_name}.{domain_name}"
            try:
                return socket.gethostbyname(fqdn)
            except socket.gaierror:
                return fqdn
        return node_name

    dns_servers = [s.strip() for s in dns_servers_str.split(",") if s.strip()]
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = dns_servers
    resolver.timeout = 3
    resolver.lifetime = 5

    if domain_name:
        try:
            ans = resolver.resolve(f"{node_name}.{domain_name}", "A")
            return str(ans[0])
        except Exception:
            pass
    try:
        ans = resolver.resolve(node_name, "A")
        return str(ans[0])
    except Exception:
        pass

    return f"{node_name}.{domain_name}" if domain_name else node_name
```

**Step 3: Write test `tests/test_winrm_collector.py`**

```python
import json
import pytest
from unittest.mock import MagicMock, patch
from tasks.collectors.winrm import run_ps, run_ps_long


def _make_session(stdout=b"", stderr=b"", status=0):
    session = MagicMock()
    result = MagicMock()
    result.status_code = status
    result.std_out = stdout
    result.std_err = stderr
    session.run_ps.return_value = result
    return session


def test_run_ps_returns_list_for_array():
    data = [{"Name": "VM1"}, {"Name": "VM2"}]
    session = _make_session(stdout=json.dumps(data).encode())
    result = run_ps(session, "Get-VM")
    assert result == data


def test_run_ps_wraps_single_dict_in_list():
    data = {"Name": "VM1"}
    session = _make_session(stdout=json.dumps(data).encode())
    result = run_ps(session, "Get-VM")
    assert result == [data]


def test_run_ps_returns_empty_list_for_null():
    session = _make_session(stdout=b"null")
    result = run_ps(session, "Get-VM")
    assert result == []


def test_run_ps_returns_none_on_error_status():
    session = _make_session(stderr=b"Access denied", status=1)
    result = run_ps(session, "Get-VM")
    assert result is None


def test_run_ps_returns_none_on_json_decode_error():
    session = _make_session(stdout=b"not json")
    result = run_ps(session, "Get-VM")
    assert result is None


def test_run_ps_propagates_os_error():
    session = MagicMock()
    session.run_ps.side_effect = OSError("connection refused")
    with pytest.raises(OSError):
        run_ps(session, "Get-VM")


def test_run_ps_long_delegates_small_scripts():
    data = [{"Name": "VM1"}]
    session = _make_session(stdout=json.dumps(data).encode())
    short_script = "Get-VM"  # well under 7000 bytes
    result = run_ps_long(session, short_script)
    assert result == data
    # Should call run_ps path (session.run_ps called once)
    assert session.run_ps.call_count == 1
```

**Step 4: Run tests**

```bash
cd /opt/hyperv_inventory && source venv/bin/activate
pytest tests/test_winrm_collector.py -v
```
Expected: 7 PASS

**Step 5: Commit**

```bash
git add tasks/collectors/__init__.py tasks/collectors/winrm.py tests/test_winrm_collector.py
git commit -m "feat: add tasks/collectors/winrm.py (WinRM session + PS execution)"
```

---

## Task 3: VM collectors (`tasks/collectors/vms.py`)

**Files:**
- Create: `tasks/collectors/vms.py`
- Test: `tests/test_vm_collectors.py`

**Step 1: Create `tasks/collectors/vms.py`**

```python
"""PowerShell scripts and collector functions for VMs."""

import logging
from typing import List, Dict, Optional
import winrm

from .winrm import run_ps

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PowerShell scripts
# ---------------------------------------------------------------------------

PS_GET_VMS = """
$ErrorActionPreference = "SilentlyContinue"
$clusterName = $null
try { $c = Get-Cluster -ErrorAction SilentlyContinue; if ($c) { $clusterName = $c.Name } } catch {}

Get-VM | ForEach-Object {
    $vm = $_
    $uptime = 0
    if ($vm.State -eq 'Running' -and $vm.Uptime) { $uptime = [int]$vm.Uptime.TotalSeconds }
    [PSCustomObject]@{
        VMId = $vm.Id
        Name = $vm.Name
        State = $vm.State.ToString()
        ComputerName = $vm.ComputerName
        CPUCount = $vm.ProcessorCount
        MemoryAssigned = [math]::Round($vm.MemoryAssigned / 1GB, 2)
        MemoryDemand = [math]::Round($vm.MemoryDemand / 1GB, 2)
        MemoryStartup = [math]::Round($vm.MemoryStartup / 1GB, 2)
        MemoryMinimum = [math]::Round($vm.MemoryMinimum / 1GB, 2)
        MemoryMaximum = [math]::Round($vm.MemoryMaximum / 1GB, 2)
        DynamicMemory = $vm.DynamicMemoryEnabled
        Generation = $vm.Generation
        Version = $vm.Version
        IntegrationServicesVersion = if ($vm.IntegrationServicesVersion) { $vm.IntegrationServicesVersion.ToString() } else { $null }
        VirtualHardDiskPath = if ($vm.VirtualHardDisks.Count -gt 0) { $vm.VirtualHardDisks[0].Path } else { $null }
        VirtualMachinePath = $vm.ConfigurationLocation
        PrimaryIPAddress = ($vm.NetworkAdapters | Where-Object {$_.IPAddresses} | ForEach-Object {$_.IPAddresses} | Where-Object {$_ -match '^\d+\.\d+\.\d+\.\d+$'} | Select-Object -First 1)
        ClusterName = $clusterName
        CreatedTime = if ($vm.CreationTime) { $vm.CreationTime.ToString("yyyy-MM-dd HH:mm:ss") } else { $null }
        UptimeSeconds = $uptime
    }
} | ConvertTo-Json -Depth 3
"""

PS_GET_VM_DISKS = """
$ErrorActionPreference = "SilentlyContinue"
Get-VM | ForEach-Object {
    $vm = $_
    Get-VMHardDiskDrive -VM $vm -ErrorAction SilentlyContinue | ForEach-Object {
        $drive = $_
        $vhd = $null
        if ($drive.Path) { try { $vhd = Get-VHD -Path $drive.Path -ErrorAction Stop } catch {} }
        $fmt = if ($vhd) { $vhd.VhdFormat.ToString() } elseif ($drive.Path -match '\.vhdx$') { 'VHDX' } elseif ($drive.Path -match '\.vhd$') { 'VHD' } else { 'Unknown' }
        [PSCustomObject]@{
            VMId = $vm.Id
            DiskName = $drive.Name
            DiskPath = $drive.Path
            DiskFormat = $fmt
            Size = if ($vhd) { [math]::Round($vhd.FileSize / 1GB, 2) } else { 0 }
            ControllerType = $drive.ControllerType.ToString()
            ControllerNumber = $drive.ControllerNumber
            ControllerLocation = $drive.ControllerLocation
        }
    }
} | ConvertTo-Json -Depth 3
"""

PS_GET_VM_NETWORKS = """
$ErrorActionPreference = "SilentlyContinue"
Get-VM | ForEach-Object {
    $vm = $_
    $vm.NetworkAdapters | ForEach-Object {
        $a = $_
        $vlan = $null
        try { $vlan = Get-VMNetworkAdapterVlan -VMNetworkAdapter $a -ErrorAction Stop } catch {}
        $vlanId = if ($vlan -and $vlan.OperationMode -eq 'Access') { $vlan.AccessVlanId } else { 0 }
        [PSCustomObject]@{
            VMId = $vm.Id
            AdapterName = $a.Name
            SwitchName = $a.SwitchName
            MacAddress = $a.MacAddress
            IPAddresses = ($a.IPAddresses | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' }) -join ','
            IsConnected = $a.Status -eq 'Ok'
            VlanId = $vlanId
        }
    }
} | ConvertTo-Json -Depth 3
"""

PS_GET_VM_SNAPSHOTS = """
$ErrorActionPreference = "SilentlyContinue"
Get-VM | ForEach-Object {
    $vm = $_
    try { Get-VMSnapshot -VMName $vm.Name -ErrorAction Stop } catch { return } | ForEach-Object {
        [PSCustomObject]@{
            VMId = $vm.Id
            SnapshotName = $_.Name
            SnapshotType = $_.SnapshotType.ToString()
            CreationTime = $_.CreationTime.ToString("yyyy-MM-dd HH:mm:ss")
            ParentSnapshotName = if ($_.ParentSnapshot) { $_.ParentSnapshot.Name } else { $null }
        }
    }
} | ConvertTo-Json -Depth 3
"""

PS_GET_VM_REPLICATION = """
$ErrorActionPreference = "SilentlyContinue"
Get-VM | ForEach-Object {
    $vm = $_
    $rep = $null
    try { $rep = $vm | Get-VMReplication -ErrorAction Stop } catch {}
    if ($rep) {
        [PSCustomObject]@{
            VMId = $vm.Id
            ReplicationState = $rep.State.ToString()
            ReplicationHealth = $rep.Health.ToString()
            ReplicationMode = $rep.ReplicationMode.ToString()
            PrimaryServer = $rep.PrimaryServer
            ReplicaServer = $rep.ReplicaServer
            FrequencySeconds = if ($rep.ReplicationFrequency) { $rep.ReplicationFrequency * 60 } else { $null }
            LastReplicationTime = if ($rep.LastReplicationTime) { $rep.LastReplicationTime.ToString("yyyy-MM-dd HH:mm:ss") } else { $null }
        }
    }
} | ConvertTo-Json -Depth 3
"""


# ---------------------------------------------------------------------------
# Collector functions
# ---------------------------------------------------------------------------

def collect_vms(session: winrm.Session) -> List[Dict]:
    return run_ps(session, PS_GET_VMS) or []

def collect_disks(session: winrm.Session) -> List[Dict]:
    return run_ps(session, PS_GET_VM_DISKS) or []

def collect_networks(session: winrm.Session) -> List[Dict]:
    return run_ps(session, PS_GET_VM_NETWORKS) or []

def collect_snapshots(session: winrm.Session) -> List[Dict]:
    return run_ps(session, PS_GET_VM_SNAPSHOTS) or []

def collect_replication(session: winrm.Session) -> List[Dict]:
    return run_ps(session, PS_GET_VM_REPLICATION) or []
```

**Step 2: Write test `tests/test_vm_collectors.py`**

```python
import json
import pytest
from unittest.mock import MagicMock, patch
from tasks.collectors.vms import collect_vms, collect_networks, PS_GET_VM_NETWORKS


def _session_returning(data):
    session = MagicMock()
    result = MagicMock()
    result.status_code = 0
    result.std_out = json.dumps(data).encode()
    result.std_err = b""
    session.run_ps.return_value = result
    return session


def test_collect_vms_returns_list():
    vms = [{"VMId": "abc", "Name": "VM1", "State": "Running"}]
    session = _session_returning(vms)
    result = collect_vms(session)
    assert len(result) == 1
    assert result[0]["Name"] == "VM1"


def test_collect_vms_returns_empty_list_on_none():
    session = MagicMock()
    result_mock = MagicMock()
    result_mock.status_code = 1
    result_mock.std_err = b"error"
    result_mock.std_out = b""
    session.run_ps.return_value = result_mock
    result = collect_vms(session)
    assert result == []


def test_collect_networks_includes_vlan():
    nets = [{"VMId": "abc", "AdapterName": "Network Adapter", "VlanId": 100}]
    session = _session_returning(nets)
    result = collect_networks(session)
    assert result[0]["VlanId"] == 100


def test_ps_get_vm_networks_script_contains_vlan():
    assert "Get-VMNetworkAdapterVlan" in PS_GET_VM_NETWORKS
    assert "AccessVlanId" in PS_GET_VM_NETWORKS
```

**Step 3: Run tests**

```bash
pytest tests/test_vm_collectors.py -v
```
Expected: 4 PASS

**Step 4: Commit**

```bash
git add tasks/collectors/vms.py tests/test_vm_collectors.py
git commit -m "feat: add tasks/collectors/vms.py (VM/disk/network/snapshot/replication PS collectors)"
```

---

## Task 4: Host collectors (`tasks/collectors/hosts.py`)

**Files:**
- Create: `tasks/collectors/hosts.py`

**Step 1: Create `tasks/collectors/hosts.py`**

```python
"""PowerShell scripts and collector functions for Hyper-V hosts."""

import logging
from typing import List, Dict, Optional
import winrm

from .winrm import run_ps

logger = logging.getLogger(__name__)

PS_GET_HOST_INFO = """
$ErrorActionPreference = "SilentlyContinue"
$clusterName = $null
try { $c = Get-Cluster -ErrorAction SilentlyContinue; if ($c) { $clusterName = $c.Name } } catch {}

$mem = Get-CimInstance -ClassName Win32_OperatingSystem
$totalMemGB  = [math]::Round($mem.TotalVisibleMemorySize / 1MB, 2)
$freeMemGB   = [math]::Round($mem.FreePhysicalMemory / 1MB, 2)

$cpuSum = (Get-CimInstance -ClassName Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
$vmCount = (Get-VM -ErrorAction SilentlyContinue).Count
$os = Get-CimInstance -ClassName Win32_OperatingSystem

$hvVersion = $null
$vhdPath = $null
$vmPath = $null
try {
    $hvHost = Get-VMHost
    $hvVersion = if ($hvHost.IntegrationServicesVersion) { $hvHost.IntegrationServicesVersion.ToString() } else { $null }
    $vhdPath = $hvHost.VirtualHardDiskPath
    $vmPath  = $hvHost.VirtualMachinePath
} catch {}

[PSCustomObject]@{
    HostName           = $env:COMPUTERNAME
    ClusterName        = $clusterName
    TotalMemoryGB      = $totalMemGB
    AvailableMemoryGB  = $freeMemGB
    LogicalProcessors  = $cpuSum
    VMCount            = $vmCount
    OSVersion          = ($os.Caption + " " + $os.Version)
    HyperVVersion      = $hvVersion
    VirtualHardDiskPath = $vhdPath
    VirtualMachinePath  = $vmPath
} | ConvertTo-Json -Depth 2
"""

PS_GET_PHYSICAL_DISKS = """
$ErrorActionPreference = "SilentlyContinue"
$disks = Get-Disk -ErrorAction SilentlyContinue
Get-PhysicalDisk -ErrorAction SilentlyContinue | ForEach-Object {
    $phys = $_
    $disk = $disks | Where-Object { $_.SerialNumber -eq $phys.SerialNumber } | Select-Object -First 1
    [PSCustomObject]@{
        FriendlyName       = $phys.FriendlyName
        SerialNumber       = $phys.SerialNumber
        MediaType          = $phys.MediaType.ToString()
        SizeGB             = [math]::Round($phys.Size / 1GB, 2)
        HealthStatus       = $phys.HealthStatus.ToString()
        OperationalStatus  = ($phys.OperationalStatus | Select-Object -First 1).ToString()
        BusType            = $phys.BusType.ToString()
        PartitionStyle     = if ($disk) { $disk.PartitionStyle.ToString() } else { 'Unknown' }
        DiskNumber         = if ($disk) { $disk.DiskNumber } else { $null }
    }
} | ConvertTo-Json -Depth 3
"""

PS_GET_CLUSTER_NODES = """
$ErrorActionPreference = "SilentlyContinue"
try {
    Get-ClusterNode -ErrorAction Stop | ForEach-Object {
        $nodeName = $_.Name
        $ip = $null
        try {
            $addr = [System.Net.Dns]::GetHostAddresses($nodeName) |
                Where-Object {
                    $_.AddressFamily -eq 'InterNetwork' -and
                    -not $_.ToString().StartsWith('169.254.') -and
                    -not $_.ToString().StartsWith('127.')
                } | Select-Object -First 1
            if ($addr) { $ip = $addr.IPAddressToString }
        } catch {}
        if (-not $ip) {
            try {
                $iface = Get-ClusterNetworkInterface -Node $nodeName -ErrorAction SilentlyContinue |
                    Where-Object { $_.State -eq 'Up' -and $_.Address -match '^\d+\.\d+\.\d+\.\d+$' } |
                    Select-Object -First 1
                if ($iface) { $ip = $iface.Address }
            } catch {}
        }
        [PSCustomObject]@{ Name = $nodeName; State = $_.State.ToString(); IPAddress = $ip }
    } | ConvertTo-Json -Depth 3
} catch {
    @() | ConvertTo-Json
}
"""


def collect_host_info(session: winrm.Session) -> Optional[Dict]:
    """Returns single host info dict or None."""
    result = run_ps(session, PS_GET_HOST_INFO)
    if result and len(result) > 0:
        return result[0]
    return None


def collect_physical_disks(session: winrm.Session) -> List[Dict]:
    return run_ps(session, PS_GET_PHYSICAL_DISKS) or []


def collect_cluster_nodes(session: winrm.Session) -> List[Dict]:
    """Returns list of {Name, State, IPAddress} dicts."""
    return run_ps(session, PS_GET_CLUSTER_NODES) or []
```

**Step 2: Commit** (no separate test needed — pattern is same as VMs, covered by integration)

```bash
git add tasks/collectors/hosts.py
git commit -m "feat: add tasks/collectors/hosts.py (host info + physical disks + cluster nodes)"
```

---

## Task 5: Storage collector (`tasks/collectors/storage.py`)

**Files:**
- Create: `tasks/collectors/storage.py`
- Test: `tests/test_storage_collector.py`

**Step 1: Create `tasks/collectors/storage.py`**

The key fix: `Get-ClusterSharedVolumeState` returns an object where the partition info is at `$state.SharedVolumeInfo.Partition`, **not** `.VolumeName.Path`. Also fix the empty-array encoding bug.

```python
"""PowerShell scripts and collector functions for cluster storage."""

import logging
from typing import List, Dict
import winrm

from .winrm import run_ps_long

logger = logging.getLogger(__name__)

PS_GET_CSV_INFO = """
$ErrorActionPreference = 'SilentlyContinue'

$volumes = Get-ClusterSharedVolume -ErrorAction SilentlyContinue
if (-not $volumes) {
    @() | ConvertTo-Json
    return
}

$results = $volumes | ForEach-Object {
    $vol = $_
    $state = $vol | Get-ClusterSharedVolumeState -ErrorAction SilentlyContinue
    if (-not $state) { return }

    $partition = $state.SharedVolumeInfo.Partition
    $volPath   = $partition.Name

    $vhdCount = 0
    $vhdMaxGB = 0.0
    $vhdActGB = 0.0

    if ($volPath) {
        Get-ChildItem -Path $volPath -Recurse -Filter *.vhdx -ErrorAction SilentlyContinue | ForEach-Object {
            $vhd = Get-VHD -Path $_.FullName -ErrorAction SilentlyContinue
            if ($vhd) {
                $vhdCount++
                $vhdMaxGB += $vhd.Size / 1GB
                $vhdActGB += $vhd.FileSize / 1GB
            }
        }
    }

    $totalGB = [math]::Round($partition.Size / 1GB, 2)
    $freeGB  = [math]::Round($partition.FreeSpace / 1GB, 2)
    $usedGB  = [math]::Round(($partition.Size - $partition.FreeSpace) / 1GB, 2)
    $pct     = if ($partition.Size -gt 0) { [math]::Round(($usedGB / $totalGB) * 100, 2) } else { 0 }
    $overPct = if ($totalGB -gt 0) { [math]::Round(($vhdMaxGB / $totalGB) * 100, 2) } else { 0 }
    $overGB  = [math]::Round($vhdMaxGB - $totalGB, 2)

    [PSCustomObject]@{
        Name                  = $vol.Name
        VolumePath            = $volPath
        OwnerNode             = $vol.OwnerNode.Name
        State                 = $vol.State.ToString()
        TotalSizeGB           = $totalGB
        FreeSpaceGB           = $freeGB
        UsedSpaceGB           = $usedGB
        PercentUsed           = $pct
        MaintenanceMode       = if ($state.MaintenanceMode) { 1 } else { 0 }
        RedirectedAccess      = if ($state.RedirectedAccess) { 1 } else { 0 }
        VHDCount              = $vhdCount
        VHDMaxSizeGB          = [math]::Round($vhdMaxGB, 2)
        VHDActualSizeGB       = [math]::Round($vhdActGB, 2)
        OversubscriptionPercent = $overPct
        OversubscriptionGB    = $overGB
    }
}

if ($results) { $results | ConvertTo-Json -Depth 3 } else { @() | ConvertTo-Json }
"""


def collect_csv_volumes(session: winrm.Session) -> List[Dict]:
    """Collect Cluster Shared Volume info from a cluster node."""
    result = run_ps_long(session, PS_GET_CSV_INFO)
    return result or []
```

**Step 2: Write test `tests/test_storage_collector.py`**

```python
import json
from unittest.mock import MagicMock
from tasks.collectors.storage import collect_csv_volumes, PS_GET_CSV_INFO


def _session_returning(data):
    session = MagicMock()
    result = MagicMock()
    result.status_code = 0
    result.std_out = json.dumps(data).encode()
    result.std_err = b""
    session.run_ps.return_value = result
    return session


def test_collect_csv_returns_list():
    csvs = [{"Name": "Cluster Disk 1", "TotalSizeGB": 1000, "FreeSpaceGB": 500}]
    session = _session_returning(csvs)
    result = collect_csv_volumes(session)
    assert len(result) == 1
    assert result[0]["Name"] == "Cluster Disk 1"


def test_collect_csv_returns_empty_list_on_error():
    session = MagicMock()
    r = MagicMock()
    r.status_code = 1
    r.std_err = b"not a cluster"
    r.std_out = b""
    session.run_ps.return_value = r
    result = collect_csv_volumes(session)
    assert result == []


def test_ps_get_csv_info_uses_correct_partition_path():
    # Verify the fixed property chain is used (not the old buggy VolumeName.Path)
    assert "VolumeName.Path" not in PS_GET_CSV_INFO
    assert "partition.Name" in PS_GET_CSV_INFO.lower() or "Partition.Name" in PS_GET_CSV_INFO
    assert "Get-ClusterSharedVolumeState" in PS_GET_CSV_INFO
```

**Step 3: Run tests**

```bash
pytest tests/test_storage_collector.py -v
```
Expected: 3 PASS

**Step 4: Commit**

```bash
git add tasks/collectors/storage.py tests/test_storage_collector.py
git commit -m "feat: add tasks/collectors/storage.py (fixed CSV PS script)"
```

---

## Task 6: Persistence layer

**Files:**
- Create: `tasks/persistence/__init__.py` (empty)
- Create: `tasks/persistence/vms.py`
- Create: `tasks/persistence/hosts.py`
- Create: `tasks/persistence/storage.py`
- Test: `tests/test_persistence.py`

**Step 1: Create `tasks/persistence/__init__.py`** — empty file.

**Step 2: Create `tasks/persistence/vms.py`**

```python
"""Persist VM-related data to SQLite."""

import logging
from datetime import datetime
from typing import List, Dict, Optional

from app.db import get_db_connection

logger = logging.getLogger(__name__)


def save_vms(vms: List[Dict], host_name: str, cluster_name: Optional[str] = None):
    if not vms:
        return
    conn = get_db_connection()
    c = conn.cursor()
    for vm in vms:
        effective_cluster = cluster_name if cluster_name is not None else vm.get("ClusterName")
        c.execute("""
            INSERT OR REPLACE INTO vm_info (
                vm_id, machine_name, host_name, cluster_name, state,
                uptime_seconds, cpu_count, memory_assigned_gb, memory_demand_gb,
                memory_startup_gb, memory_minimum_gb, memory_maximum_gb,
                dynamic_memory_enabled, generation, version, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            vm.get("VMId"), vm.get("Name"), host_name, effective_cluster,
            vm.get("State"), vm.get("UptimeSeconds", 0), vm.get("CPUCount", 0),
            vm.get("MemoryAssigned", 0), vm.get("MemoryDemand", 0),
            vm.get("MemoryStartup", 0), vm.get("MemoryMinimum", 0),
            vm.get("MemoryMaximum", 0),
            1 if vm.get("DynamicMemory") else 0,
            vm.get("Generation", 0), vm.get("Version"),
            vm.get("CreatedTime"),
        ))
    conn.commit()
    conn.close()
    logger.info(f"Saved {len(vms)} VMs for {host_name}")


def save_disks(disks: List[Dict]):
    if not disks:
        return
    conn = get_db_connection()
    c = conn.cursor()
    for d in disks:
        c.execute("""
            INSERT OR REPLACE INTO vm_disks
            (vm_id, disk_name, disk_path, disk_format, size_gb, controller_type, controller_number, controller_location)
            VALUES (?,?,?,?,?,?,?,?)
        """, (d.get("VMId"), d.get("DiskName"), d.get("DiskPath"), d.get("DiskFormat"),
              d.get("Size", 0), d.get("ControllerType"), d.get("ControllerNumber"), d.get("ControllerLocation")))
    conn.commit()
    conn.close()


def save_networks(networks: List[Dict]):
    if not networks:
        return
    conn = get_db_connection()
    c = conn.cursor()
    for n in networks:
        c.execute("""
            INSERT OR REPLACE INTO vm_network_adapters
            (vm_id, adapter_name, switch_name, mac_address, ip_addresses, is_connected, vlan_id, bandwidth_setting)
            VALUES (?,?,?,?,?,?,?,?)
        """, (n.get("VMId"), n.get("AdapterName"), n.get("SwitchName"), n.get("MacAddress"),
              n.get("IPAddresses"), 1 if n.get("IsConnected") else 0, n.get("VlanId", 0), n.get("BandwidthMode")))
    conn.commit()
    conn.close()


def save_snapshots(snapshots: List[Dict]):
    if not snapshots:
        return
    conn = get_db_connection()
    c = conn.cursor()
    for s in snapshots:
        c.execute("""
            INSERT OR REPLACE INTO vm_snapshots
            (vm_id, snapshot_name, snapshot_type, creation_time, parent_snapshot_id)
            VALUES (?,?,?,?,?)
        """, (s.get("VMId"), s.get("SnapshotName"), s.get("SnapshotType"),
              s.get("CreationTime"), s.get("ParentSnapshotName")))
    conn.commit()
    conn.close()


def save_replication(reps: List[Dict]):
    if not reps:
        return
    conn = get_db_connection()
    c = conn.cursor()
    for r in reps:
        c.execute("""
            INSERT OR REPLACE INTO vm_replication
            (vm_id, replication_state, replication_health, replication_mode,
             primary_server, replica_server, frequency_seconds, last_replication_time)
            VALUES (?,?,?,?,?,?,?,?)
        """, (r.get("VMId"), r.get("ReplicationState"), r.get("ReplicationHealth"),
              r.get("ReplicationMode"), r.get("PrimaryServer"), r.get("ReplicaServer"),
              r.get("FrequencySeconds"), r.get("LastReplicationTime")))
    conn.commit()
    conn.close()
```

**Step 3: Create `tasks/persistence/hosts.py`**

```python
"""Persist host and physical disk data to SQLite."""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from app.db import get_db_connection

logger = logging.getLogger(__name__)


def save_host(host_info: Dict, cluster_name: Optional[str] = None, connection_ip: Optional[str] = None):
    if not host_info:
        return
    conn = get_db_connection()
    effective_cluster = cluster_name if cluster_name is not None else host_info.get("ClusterName")
    conn.execute("""
        INSERT INTO hyperv_hosts (
            host_name, cluster_name, total_memory_gb, available_memory_gb,
            logical_processors, vm_count, os_version, hyperv_version,
            vhd_default_path, vm_default_path, connection_ip, last_updated
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(host_name) DO UPDATE SET
            cluster_name        = excluded.cluster_name,
            total_memory_gb     = excluded.total_memory_gb,
            available_memory_gb = excluded.available_memory_gb,
            logical_processors  = excluded.logical_processors,
            vm_count            = excluded.vm_count,
            os_version          = excluded.os_version,
            hyperv_version      = excluded.hyperv_version,
            vhd_default_path    = excluded.vhd_default_path,
            vm_default_path     = excluded.vm_default_path,
            connection_ip       = excluded.connection_ip,
            last_updated        = excluded.last_updated
    """, (
        host_info.get("HostName"), effective_cluster,
        host_info.get("TotalMemoryGB", 0), host_info.get("AvailableMemoryGB", 0),
        host_info.get("LogicalProcessors", 0), host_info.get("VMCount", 0),
        host_info.get("OSVersion"), host_info.get("HyperVVersion"),
        host_info.get("VirtualHardDiskPath"), host_info.get("VirtualMachinePath"),
        connection_ip, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))
    conn.commit()
    conn.close()
    logger.info(f"Saved host {host_info.get('HostName')}")


def save_physical_disks(disks: List[Dict], host_name: str):
    if not disks:
        return
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Clear old disks for this host then re-insert (disk list is authoritative)
    c.execute("DELETE FROM host_physical_disks WHERE host_name = ?", (host_name,))
    for d in disks:
        c.execute("""
            INSERT OR REPLACE INTO host_physical_disks
            (host_name, friendly_name, serial_number, media_type, size_gb,
             health_status, operational_status, bus_type, partition_style, disk_number, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (host_name, d.get("FriendlyName"), d.get("SerialNumber"), d.get("MediaType"),
              d.get("SizeGB", 0), d.get("HealthStatus"), d.get("OperationalStatus"),
              d.get("BusType"), d.get("PartitionStyle"), d.get("DiskNumber"), now))
    conn.commit()
    conn.close()
    logger.info(f"Saved {len(disks)} physical disks for {host_name}")
```

**Step 4: Create `tasks/persistence/storage.py`**

```python
"""Persist cluster storage data to SQLite."""

import logging
from datetime import datetime
from typing import List, Dict

from app.db import get_db_connection

logger = logging.getLogger(__name__)


def save_csv_volumes(csv_list: List[Dict], cluster_name: str):
    if not csv_list:
        return
    conn = get_db_connection()
    c = conn.cursor()
    for vol in csv_list:
        c.execute("""
            INSERT OR REPLACE INTO cluster_shared_volumes (
                name, volume_path, owner_node, state,
                total_size_gb, free_space_gb, used_space_gb, percent_used,
                maintenance_mode, redirected_access,
                vhd_count, vhd_max_size_gb, vhd_actual_size_gb,
                oversubscription_percent, oversubscription_gb,
                cluster_name, last_updated
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            vol.get("Name"), vol.get("VolumePath"), vol.get("OwnerNode"), vol.get("State"),
            vol.get("TotalSizeGB", 0), vol.get("FreeSpaceGB", 0), vol.get("UsedSpaceGB", 0),
            vol.get("PercentUsed", 0),
            1 if vol.get("MaintenanceMode") else 0,
            1 if vol.get("RedirectedAccess") else 0,
            vol.get("VHDCount", 0), vol.get("VHDMaxSizeGB", 0), vol.get("VHDActualSizeGB", 0),
            vol.get("OversubscriptionPercent", 0), vol.get("OversubscriptionGB", 0),
            cluster_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
    conn.commit()
    conn.close()
    logger.info(f"Saved {len(csv_list)} CSV volumes for cluster '{cluster_name}'")
```

**Step 5: Write test `tests/test_persistence.py`**

```python
import sqlite3
import pytest
from unittest.mock import patch
import app.db as db_module
from tasks.persistence.vms import save_vms
from tasks.persistence.hosts import save_host, save_physical_disks
from tasks.persistence.storage import save_csv_volumes


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    with patch.object(db_module, "DATABASE_PATH", db_path):
        db_module.init_db()
        # Also patch persistence modules
        import tasks.persistence.vms as pv
        import tasks.persistence.hosts as ph
        import tasks.persistence.storage as ps
        with patch.object(pv, "get_db_connection", db_module.get_db_connection), \
             patch.object(ph, "get_db_connection", db_module.get_db_connection), \
             patch.object(ps, "get_db_connection", db_module.get_db_connection):
            yield db_path


def test_save_vms_inserts_rows(db):
    vms = [{"VMId": "vm1", "Name": "prod-web", "State": "Running",
            "CPUCount": 4, "MemoryAssigned": 8.0}]
    save_vms(vms, "HOST-01", cluster_name="PROD")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT * FROM vm_info WHERE vm_id='vm1'").fetchone()
    conn.close()
    assert row is not None


def test_save_vms_uses_explicit_cluster_name(db):
    vms = [{"VMId": "vm2", "Name": "test", "State": "Off", "ClusterName": "WRONG"}]
    save_vms(vms, "HOST-02", cluster_name="CORRECT")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT cluster_name FROM vm_info WHERE vm_id='vm2'").fetchone()
    conn.close()
    assert row[0] == "CORRECT"


def test_save_host_upserts(db):
    info = {"HostName": "NODE1", "TotalMemoryGB": 128.0, "AvailableMemoryGB": 64.0,
            "LogicalProcessors": 32, "VMCount": 10, "OSVersion": "Windows Server 2022"}
    save_host(info, cluster_name="PROD", connection_ip="10.0.0.1")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT connection_ip FROM hyperv_hosts WHERE host_name='NODE1'").fetchone()
    conn.close()
    assert row[0] == "10.0.0.1"


def test_save_physical_disks(db):
    disks = [{"FriendlyName": "SEAGATE ST1000", "SerialNumber": "SN001",
              "MediaType": "HDD", "SizeGB": 1000.0, "HealthStatus": "Healthy",
              "OperationalStatus": "Online", "BusType": "SAS", "PartitionStyle": "GPT"}]
    save_physical_disks(disks, "NODE1")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT * FROM host_physical_disks WHERE host_name='NODE1'").fetchone()
    conn.close()
    assert row is not None


def test_save_csv_volumes(db):
    vols = [{"Name": "Cluster Disk 1", "TotalSizeGB": 2000.0, "FreeSpaceGB": 500.0,
             "PercentUsed": 75.0}]
    save_csv_volumes(vols, "PROD")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT cluster_name FROM cluster_shared_volumes WHERE name='Cluster Disk 1'").fetchone()
    conn.close()
    assert row[0] == "PROD"
```

**Step 6: Run tests**

```bash
pytest tests/test_persistence.py -v
```
Expected: 5 PASS

**Step 7: Commit**

```bash
git add tasks/persistence/ tests/test_persistence.py
git commit -m "feat: add tasks/persistence layer (vms, hosts, storage)"
```

---

## Task 7: Change detection monitor (`tasks/monitor.py`)

**Files:**
- Create: `tasks/monitor.py`
- Test: `tests/test_monitor.py`

**Step 1: Create `tasks/monitor.py`**

```python
"""Change detection: compare old DB snapshot vs new sync data, generate notifications."""

import logging
from datetime import datetime
from typing import Dict, List, Any

from app.db import get_db_connection

logger = logging.getLogger(__name__)


def snapshot_vm_states() -> Dict[str, Dict]:
    """Capture current VM state from DB before sync overwrites it.

    Returns dict of vm_id -> {state, cpu_count, memory_gb, ip, disk_count, name, cluster}
    """
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT vi.vm_id, vi.machine_name, vi.state, vi.cpu_count,
               vi.memory_assigned_gb, vi.cluster_name,
               (SELECT ip_addresses FROM vm_network_adapters
                WHERE vm_id = vi.vm_id AND ip_addresses != '' LIMIT 1) as ip,
               (SELECT COUNT(*) FROM vm_disks WHERE vm_id = vi.vm_id) as disk_count
        FROM vm_info vi
    """).fetchall()
    conn.close()
    return {
        r["vm_id"]: {
            "name": r["machine_name"],
            "state": r["state"],
            "cpu_count": r["cpu_count"],
            "memory_gb": r["memory_assigned_gb"],
            "cluster": r["cluster_name"],
            "ip": r["ip"],
            "disk_count": r["disk_count"],
        }
        for r in rows
    }


def snapshot_csv_states() -> Dict[str, Dict]:
    """Capture current CSV free-space state before sync.

    Returns dict keyed by (name, cluster_name) tuple serialized as "name||cluster".
    """
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT name, cluster_name, free_space_gb, percent_used FROM cluster_shared_volumes"
    ).fetchall()
    conn.close()
    return {
        f"{r['name']}||{r['cluster_name']}": {
            "name": r["name"],
            "cluster": r["cluster_name"],
            "free_gb": r["free_space_gb"],
            "pct_used": r["percent_used"],
        }
        for r in rows
    }


def detect_vm_changes(old_snapshot: Dict[str, Dict], storage_threshold_pct: float = 20.0) -> List[Dict]:
    """Compare old snapshot against freshly-written vm_info rows.

    Returns list of change event dicts.
    """
    conn = get_db_connection()
    new_rows = conn.execute("""
        SELECT vi.vm_id, vi.machine_name, vi.state, vi.cpu_count,
               vi.memory_assigned_gb, vi.cluster_name,
               (SELECT ip_addresses FROM vm_network_adapters
                WHERE vm_id = vi.vm_id AND ip_addresses != '' LIMIT 1) as ip,
               (SELECT COUNT(*) FROM vm_disks WHERE vm_id = vi.vm_id) as disk_count
        FROM vm_info vi
    """).fetchall()
    conn.close()

    new_snapshot = {
        r["vm_id"]: {
            "name": r["machine_name"],
            "state": r["state"],
            "cpu_count": r["cpu_count"],
            "memory_gb": r["memory_assigned_gb"],
            "cluster": r["cluster_name"],
            "ip": r["ip"],
            "disk_count": r["disk_count"],
        }
        for r in new_rows
    }

    events = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Detect new VMs
    for vm_id, new in new_snapshot.items():
        if vm_id not in old_snapshot:
            events.append({
                "vm_id": vm_id, "machine_name": new["name"],
                "change_type": "created", "field_name": None,
                "old_value": None, "new_value": new["state"],
                "cluster_name": new["cluster"],
                "severity": "info",
                "message": f"New VM '{new['name']}' discovered on cluster {new['cluster']}",
                "detected_at": now,
            })

    # Detect deleted VMs
    for vm_id, old in old_snapshot.items():
        if vm_id not in new_snapshot:
            events.append({
                "vm_id": vm_id, "machine_name": old["name"],
                "change_type": "deleted", "field_name": None,
                "old_value": old["state"], "new_value": None,
                "cluster_name": old["cluster"],
                "severity": "critical",
                "message": f"VM '{old['name']}' no longer visible on cluster {old['cluster']}",
                "detected_at": now,
            })

    # Detect changes on existing VMs
    for vm_id, new in new_snapshot.items():
        if vm_id not in old_snapshot:
            continue
        old = old_snapshot[vm_id]

        checks = [
            ("state", "state_change", "warning",
             lambda o, n: f"VM '{new['name']}': state {o} → {n}"),
            ("cpu_count", "cpu", "warning",
             lambda o, n: f"VM '{new['name']}': CPU {o} → {n}"),
            ("memory_gb", "memory", "warning",
             lambda o, n: f"VM '{new['name']}': RAM {o}GB → {n}GB"),
            ("ip", "ip_change", "info",
             lambda o, n: f"VM '{new['name']}': IP {o} → {n}"),
            ("disk_count", "disk_change", "info",
             lambda o, n: f"VM '{new['name']}': disk count {o} → {n}"),
        ]
        for field, change_type, severity, msg_fn in checks:
            old_val = old.get(field)
            new_val = new.get(field)
            if old_val != new_val and not (old_val is None and new_val is None):
                events.append({
                    "vm_id": vm_id, "machine_name": new["name"],
                    "change_type": change_type, "field_name": field,
                    "old_value": str(old_val), "new_value": str(new_val),
                    "cluster_name": new["cluster"],
                    "severity": severity,
                    "message": msg_fn(old_val, new_val),
                    "detected_at": now,
                })

    return events


def detect_storage_changes(old_csv_snapshot: Dict[str, Dict], threshold_pct: float = 20.0) -> List[Dict]:
    """Check current CSVs for low free space."""
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT name, cluster_name, free_space_gb, percent_used FROM cluster_shared_volumes"
    ).fetchall()
    conn.close()

    events = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    free_threshold = 100.0 - threshold_pct  # percent_used threshold

    for row in rows:
        pct_used = row["percent_used"] or 0
        if pct_used >= free_threshold:
            pct_free = round(100 - pct_used, 1)
            events.append({
                "vm_id": None,
                "machine_name": row["name"],
                "change_type": "storage_low",
                "field_name": "percent_used",
                "old_value": None,
                "new_value": str(pct_used),
                "cluster_name": row["cluster_name"],
                "severity": "warning",
                "message": (
                    f"CSV '{row['name']}' on cluster {row['cluster_name']}: "
                    f"only {pct_free}% free (threshold {threshold_pct}%)"
                ),
                "detected_at": now,
            })

    return events


def persist_events(events: List[Dict]):
    """Write change events to vm_history and notifications tables."""
    if not events:
        return
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for ev in events:
        # Write to vm_history (skip storage_low — not VM-specific)
        if ev["change_type"] != "storage_low" and ev.get("vm_id"):
            try:
                c.execute("""
                    INSERT OR IGNORE INTO vm_history
                    (vm_id, machine_name, change_type, field_name, old_value, new_value,
                     change_description, detected_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (
                    ev["vm_id"], ev["machine_name"], ev["change_type"],
                    ev.get("field_name"), ev.get("old_value"), ev.get("new_value"),
                    ev["message"], ev["detected_at"],
                ))
            except Exception as e:
                logger.warning(f"Could not write vm_history: {e}")

        # Always write to notifications
        c.execute("""
            INSERT INTO notifications
            (notification_type, vm_id, machine_name, message, severity, cluster_name, is_read, created_at)
            VALUES (?,?,?,?,?,?,0,?)
        """, (
            ev["change_type"], ev.get("vm_id"), ev["machine_name"],
            ev["message"], ev["severity"], ev.get("cluster_name"), now,
        ))

    conn.commit()
    conn.close()
    logger.info(f"Persisted {len(events)} change events")


def get_storage_threshold() -> float:
    """Read low-storage threshold from settings table (default 20%)."""
    try:
        conn = get_db_connection()
        row = conn.execute(
            "SELECT value FROM settings WHERE key='storage_threshold_pct'"
        ).fetchone()
        conn.close()
        return float(row["value"]) if row else 20.0
    except Exception:
        return 20.0
```

**Step 2: Write test `tests/test_monitor.py`**

```python
import sqlite3
import pytest
from unittest.mock import patch
import app.db as db_module
from tasks.monitor import (
    detect_vm_changes, detect_storage_changes, persist_events
)
import tasks.monitor as monitor_module


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    with patch.object(db_module, "DATABASE_PATH", db_path), \
         patch.object(monitor_module, "get_db_connection", db_module.get_db_connection):
        db_module.init_db()
        yield db_path


def _insert_vm(db_path, vm_id, name, state, cpu, mem, cluster):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR REPLACE INTO vm_info
        (vm_id, machine_name, state, cpu_count, memory_assigned_gb, cluster_name, host_name)
        VALUES (?,?,?,?,?,?,?)
    """, (vm_id, name, state, cpu, mem, cluster, "HOST-01"))
    conn.commit()
    conn.close()


def _insert_csv(db_path, name, cluster, pct_used):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR REPLACE INTO cluster_shared_volumes
        (name, cluster_name, percent_used, free_space_gb, total_size_gb)
        VALUES (?,?,?,?,?)
    """, (name, cluster, pct_used, 100 * (1 - pct_used/100), 1000))
    conn.commit()
    conn.close()


def test_detect_new_vm(db):
    _insert_vm(db, "vm1", "prod-web", "Running", 4, 8.0, "PROD")
    old_snapshot = {}  # empty — vm1 is new
    events = detect_vm_changes(old_snapshot)
    assert any(e["change_type"] == "created" and e["vm_id"] == "vm1" for e in events)


def test_detect_deleted_vm(db):
    # vm1 is in old snapshot but not in DB (already cleared before sync)
    old_snapshot = {"vm-gone": {"name": "old-vm", "state": "Running",
                                "cpu_count": 2, "memory_gb": 4.0,
                                "cluster": "PROD", "ip": None, "disk_count": 1}}
    events = detect_vm_changes(old_snapshot)
    assert any(e["change_type"] == "deleted" and e["vm_id"] == "vm-gone" for e in events)


def test_detect_state_change(db):
    _insert_vm(db, "vm1", "prod-web", "Off", 4, 8.0, "PROD")
    old_snapshot = {"vm1": {"name": "prod-web", "state": "Running",
                             "cpu_count": 4, "memory_gb": 8.0,
                             "cluster": "PROD", "ip": None, "disk_count": 1}}
    events = detect_vm_changes(old_snapshot)
    state_events = [e for e in events if e["change_type"] == "state_change"]
    assert len(state_events) == 1
    assert state_events[0]["old_value"] == "Running"
    assert state_events[0]["new_value"] == "Off"


def test_detect_cpu_change(db):
    _insert_vm(db, "vm1", "prod-web", "Running", 8, 8.0, "PROD")
    old_snapshot = {"vm1": {"name": "prod-web", "state": "Running",
                             "cpu_count": 4, "memory_gb": 8.0,
                             "cluster": "PROD", "ip": None, "disk_count": 1}}
    events = detect_vm_changes(old_snapshot)
    assert any(e["change_type"] == "cpu" for e in events)


def test_detect_storage_low(db):
    _insert_csv(db, "Cluster Disk 1", "PROD", 85.0)  # 15% free, threshold 20%
    events = detect_storage_changes({}, threshold_pct=20.0)
    assert len(events) == 1
    assert events[0]["change_type"] == "storage_low"
    assert "Cluster Disk 1" in events[0]["message"]


def test_detect_storage_ok(db):
    _insert_csv(db, "Cluster Disk 1", "PROD", 50.0)  # 50% free
    events = detect_storage_changes({}, threshold_pct=20.0)
    assert len(events) == 0


def test_persist_events_writes_notification(db):
    events = [{
        "vm_id": "vm1", "machine_name": "prod-web",
        "change_type": "state_change", "field_name": "state",
        "old_value": "Running", "new_value": "Off",
        "cluster_name": "PROD", "severity": "warning",
        "message": "VM prod-web: Running → Off",
        "detected_at": "2026-02-22 12:00:00",
    }]
    persist_events(events)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT * FROM notifications").fetchall()
    history = conn.execute("SELECT * FROM vm_history").fetchall()
    conn.close()
    assert len(rows) == 1
    assert len(history) == 1
```

**Step 3: Run tests**

```bash
pytest tests/test_monitor.py -v
```
Expected: 8 PASS

**Step 4: Commit**

```bash
git add tasks/monitor.py tests/test_monitor.py
git commit -m "feat: add tasks/monitor.py (VM + storage change detection)"
```

---

## Task 8: Notification delivery (`notifications/`)

**Files:**
- Create: `notifications/__init__.py`
- Create: `notifications/email.py`
- Create: `notifications/webhook.py`
- Test: `tests/test_notifications.py`

**Step 1: Create `notifications/__init__.py`**

```python
"""Notification delivery: in-app (DB), email, webhook."""

import logging
from typing import List, Dict

from app.db import get_db_connection

logger = logging.getLogger(__name__)


def dispatch_notifications(events: List[Dict]):
    """Deliver change events via all configured channels.

    In-app: always (written by monitor.persist_events — call that first).
    Email: if enable_email_alerts=1 and SMTP configured.
    Webhook: if enable_webhook_alerts=1 and WEBHOOK_URL configured.
    """
    if not events:
        return

    settings = _load_settings()

    if settings.get("enable_email_alerts") == "1":
        from .email import send_email_alerts
        try:
            send_email_alerts(events, settings)
        except Exception as e:
            logger.error(f"Email delivery failed: {e}")

    if settings.get("enable_webhook_alerts") == "1":
        from .webhook import send_webhook_alerts
        try:
            send_webhook_alerts(events, settings)
        except Exception as e:
            logger.error(f"Webhook delivery failed: {e}")


def _load_settings() -> Dict[str, str]:
    try:
        conn = get_db_connection()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
        return {r["key"]: r["value"] for r in rows}
    except Exception:
        return {}
```

**Step 2: Create `notifications/email.py`**

```python
"""Email alert delivery via SMTP."""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict

logger = logging.getLogger(__name__)

SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟡", "info": "🔵"}


def send_email_alerts(events: List[Dict], settings: Dict[str, str]):
    """Send one email summarizing all change events."""
    smtp_host = os.getenv("SMTP_HOST") or settings.get("smtp_host", "")
    smtp_port = int(os.getenv("SMTP_PORT") or settings.get("smtp_port", "587"))
    smtp_user = os.getenv("SMTP_USER") or settings.get("smtp_user", "")
    smtp_pass = os.getenv("SMTP_PASSWORD") or settings.get("smtp_password", "")
    to_addr = os.getenv("ALERT_EMAIL_TO") or settings.get("alert_email_to", "")

    if not all([smtp_host, smtp_user, smtp_pass, to_addr]):
        logger.warning("Email not configured — skipping (set SMTP_HOST, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO)")
        return

    subject = f"[HyperV Inventory] {len(events)} change(s) detected"

    lines = []
    for ev in events:
        emoji = SEVERITY_EMOJI.get(ev.get("severity", "info"), "🔵")
        lines.append(f"{emoji} {ev['message']}")

    body = "\n".join(lines)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to_addr], msg.as_string())

    logger.info(f"Sent email alert with {len(events)} events to {to_addr}")
```

**Step 3: Create `notifications/webhook.py`**

```python
"""Webhook alert delivery (Slack / Teams / generic HTTP)."""

import os
import json
import logging
from typing import List, Dict

import requests

logger = logging.getLogger(__name__)


def send_webhook_alerts(events: List[Dict], settings: Dict[str, str]):
    """POST each event as JSON to the configured webhook URL."""
    url = os.getenv("WEBHOOK_URL") or settings.get("webhook_url", "")
    if not url:
        logger.warning("WEBHOOK_URL not configured — skipping webhook delivery")
        return

    for ev in events:
        payload = {
            "type": ev.get("change_type"),
            "severity": ev.get("severity", "info"),
            "message": ev.get("message"),
            "vm_id": ev.get("vm_id"),
            "machine_name": ev.get("machine_name"),
            "cluster": ev.get("cluster_name"),
            "old_value": ev.get("old_value"),
            "new_value": ev.get("new_value"),
            "timestamp": ev.get("detected_at"),
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Webhook POST failed for event '{ev.get('message')}': {e}")

    logger.info(f"Sent {len(events)} webhook events to {url}")
```

**Step 4: Write test `tests/test_notifications.py`**

```python
import pytest
from unittest.mock import patch, MagicMock, call
from notifications import dispatch_notifications
from notifications.email import send_email_alerts
from notifications.webhook import send_webhook_alerts


SAMPLE_EVENTS = [
    {"change_type": "state_change", "severity": "warning",
     "message": "VM prod-web: Running → Off",
     "vm_id": "vm1", "machine_name": "prod-web", "cluster_name": "PROD",
     "old_value": "Running", "new_value": "Off", "detected_at": "2026-02-22 12:00:00"},
]


def test_dispatch_calls_email_when_enabled():
    settings = {"enable_email_alerts": "1", "enable_webhook_alerts": "0"}
    with patch("notifications._load_settings", return_value=settings), \
         patch("notifications.email.send_email_alerts") as mock_email:
        dispatch_notifications(SAMPLE_EVENTS)
        mock_email.assert_called_once()


def test_dispatch_calls_webhook_when_enabled():
    settings = {"enable_email_alerts": "0", "enable_webhook_alerts": "1"}
    with patch("notifications._load_settings", return_value=settings), \
         patch("notifications.webhook.send_webhook_alerts") as mock_wh:
        dispatch_notifications(SAMPLE_EVENTS)
        mock_wh.assert_called_once()


def test_dispatch_skips_both_when_disabled():
    settings = {"enable_email_alerts": "0", "enable_webhook_alerts": "0"}
    with patch("notifications._load_settings", return_value=settings), \
         patch("notifications.email.send_email_alerts") as mock_email, \
         patch("notifications.webhook.send_webhook_alerts") as mock_wh:
        dispatch_notifications(SAMPLE_EVENTS)
        mock_email.assert_not_called()
        mock_wh.assert_not_called()


def test_dispatch_empty_events_is_noop():
    with patch("notifications._load_settings") as mock_settings:
        dispatch_notifications([])
        mock_settings.assert_not_called()


def test_webhook_posts_json():
    settings = {"webhook_url": "http://hooks.example.com/test"}
    with patch("notifications.webhook.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()
        send_webhook_alerts(SAMPLE_EVENTS, settings)
        assert mock_post.call_count == 1
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["type"] == "state_change"
        assert kwargs["json"]["severity"] == "warning"
```

**Step 5: Run tests**

```bash
pytest tests/test_notifications.py -v
```
Expected: 5 PASS

**Step 6: Commit**

```bash
git add notifications/ tests/test_notifications.py
git commit -m "feat: add notifications layer (in-app, email, webhook delivery)"
```

---

## Task 9: Orchestrator (`tasks/orchestrator.py`)

**Files:**
- Create: `tasks/orchestrator.py`

**What this does:** Replaces `tasks/sync.py`. Keeps the same Celery task names (using `name=` parameter) so existing queue entries still work. Wires collectors → persistence → monitor → notifications.

**Step 1: Create `tasks/orchestrator.py`**

```python
"""Celery tasks: orchestrate Hyper-V data collection, change detection, notifications."""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from celery import shared_task, group, chord

from app.db import get_db_connection
from tasks.collectors.winrm import create_winrm_session, resolve_node_ip
from tasks.collectors.hosts import collect_cluster_nodes, PS_GET_CLUSTER_NODES
from tasks.collectors.vms import collect_vms, collect_disks, collect_networks, collect_snapshots, collect_replication
from tasks.collectors.hosts import collect_host_info, collect_physical_disks
from tasks.collectors.storage import collect_csv_volumes
from tasks.persistence.vms import save_vms, save_disks, save_networks, save_snapshots, save_replication
from tasks.persistence.hosts import save_host, save_physical_disks
from tasks.persistence.storage import save_csv_volumes
from tasks.monitor import (
    snapshot_vm_states, snapshot_csv_states,
    detect_vm_changes, detect_storage_changes,
    persist_events, get_storage_threshold,
)
from notifications import dispatch_notifications

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _update_sync_metadata(status: str, **kwargs):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        fields = [f"{k} = ?" for k in kwargs if k != "start"]
        values = [kwargs[k] for k in kwargs if k != "start"]
        if kwargs.get("start"):
            c.execute("""
                INSERT OR REPLACE INTO sync_metadata
                (id, last_sync_start, last_sync_status, hosts_discovered, hosts_processed)
                VALUES (1, ?, 'running', ?, 0)
            """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), kwargs.get("hosts_discovered", 0)))
        else:
            fields.insert(0, "last_sync_end = ?")
            fields.insert(1, "last_sync_status = ?")
            values = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status] + values
            fields_sql = ", ".join(fields)
            values.append(1)
            c.execute(f"UPDATE sync_metadata SET {fields_sql} WHERE id = 1", values)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"sync_metadata update failed: {e}")


def _clear_vm_data():
    conn = get_db_connection()
    for tbl in ("vm_info", "vm_disks", "vm_network_adapters", "vm_snapshots", "vm_replication"):
        conn.execute(f"DELETE FROM {tbl}")
    conn.commit()
    conn.close()


def _discover_cluster_nodes(cluster_id: int, cluster_name: str) -> Dict[str, str]:
    """Connect to cluster FQDN, run Get-ClusterNode, resolve IPs. Returns {node_name: ip}."""
    conn = get_db_connection()
    row = conn.execute(
        "SELECT domain, dns_servers, domain_name FROM clusters WHERE id = ?", (cluster_id,)
    ).fetchone()
    conn.close()

    domain = (row["domain"] if row else None) or cluster_name
    dns_servers = row["dns_servers"] if row else None
    domain_name = row["domain_name"] if row else None

    logger.info(f"Connecting to cluster FQDN: {domain}")
    session = create_winrm_session(domain, cluster_id=cluster_id)
    nodes = collect_cluster_nodes(session)

    if not nodes:
        raise RuntimeError(f"Get-ClusterNode returned no results from {domain}")

    node_map = {}
    for node in nodes:
        if node.get("State") != "Up":
            continue
        name = node.get("Name", "")
        ip = node.get("IPAddress") or resolve_node_ip(name, dns_servers, domain_name)
        node_map[name] = ip

    if not node_map:
        raise RuntimeError(f"No Up nodes in cluster {cluster_name}")
    return node_map


# ---------------------------------------------------------------------------
# Celery tasks (names kept for backward compatibility)
# ---------------------------------------------------------------------------

@shared_task(bind=True, name="tasks.sync.fetch_hyperv_data")
def fetch_hyperv_data(self):
    """Top-level orchestrator: discover nodes, dispatch per-host + per-cluster tasks."""
    logger.info("Starting Hyper-V inventory sync")

    conn = get_db_connection()
    clusters = conn.execute(
        "SELECT id, cluster_name, domain FROM clusters WHERE is_enabled = 1"
    ).fetchall()
    conn.close()

    if not clusters:
        msg = "No enabled clusters configured"
        logger.error(msg)
        _update_sync_metadata("error", errors=msg)
        return {"status": "error", "message": msg}

    # Snapshot current state BEFORE clearing (for change detection)
    vm_snapshot = snapshot_vm_states()
    csv_snapshot = snapshot_csv_states()
    logger.info(f"Snapshot: {len(vm_snapshot)} VMs, {len(csv_snapshot)} CSV volumes")

    task_sigs = []
    cluster_info = []
    node_to_cluster = {}
    total_nodes = 0

    for row in clusters:
        cluster_id, cluster_name = row["id"], row["cluster_name"]
        try:
            node_map = _discover_cluster_nodes(cluster_id, cluster_name)
            node_ips = list(node_map.values())
            cluster_info.append((cluster_id, cluster_name, node_ips))
            total_nodes += len(node_ips)
            for ip in node_ips:
                node_to_cluster[ip] = cluster_id
            logger.info(f"Cluster {cluster_name}: {len(node_ips)} node(s)")
        except Exception as e:
            logger.error(f"Node discovery failed for {cluster_name}: {e}")

    if total_nodes == 0:
        msg = "No nodes discovered from any cluster"
        logger.error(msg)
        _update_sync_metadata("error", errors=msg)
        return {"status": "error", "message": msg}

    _update_sync_metadata("running", start=True, hosts_discovered=total_nodes)
    _clear_vm_data()

    for ip, cid in node_to_cluster.items():
        task_sigs.append(fetch_single_host.s(ip, cid))
    for cid, cname, node_ips in cluster_info:
        task_sigs.append(fetch_cluster_csv_storage.s(cid, cname, node_ips))

    callback = aggregate_and_monitor.s(
        vm_snapshot=vm_snapshot, csv_snapshot=csv_snapshot
    )

    try:
        chord(group(task_sigs))(callback)
        return {"status": "started", "nodes": total_nodes, "clusters": len(cluster_info)}
    except Exception as e:
        logger.error(f"Failed to dispatch sync tasks: {e}")
        _update_sync_metadata("error", errors=str(e))
        return {"status": "error", "message": str(e)}


@shared_task(name="tasks.sync.fetch_single_host")
def fetch_single_host(host_ip: str, cluster_id: int = None):
    """Collect all data from one Hyper-V host node."""
    logger.info(f"[Host] Collecting from {host_ip}")

    cluster_name = None
    if cluster_id:
        try:
            conn = get_db_connection()
            row = conn.execute("SELECT cluster_name FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
            conn.close()
            if row:
                cluster_name = row["cluster_name"]
        except Exception:
            pass

    try:
        session = create_winrm_session(host_ip, cluster_id=cluster_id)

        vms = collect_vms(session)
        save_vms(vms, host_ip, cluster_name=cluster_name)

        save_disks(collect_disks(session))
        save_networks(collect_networks(session))
        save_snapshots(collect_snapshots(session))
        save_replication(collect_replication(session))

        host_info = collect_host_info(session)
        save_host(host_info, cluster_name=cluster_name, connection_ip=host_ip)

        phys_disks = collect_physical_disks(session)
        if host_info and phys_disks:
            save_physical_disks(phys_disks, host_info.get("HostName", host_ip))

        try:
            conn = get_db_connection()
            conn.execute(
                "UPDATE sync_metadata SET hosts_processed = hosts_processed + 1 WHERE id = 1"
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

        logger.info(f"[Host] Done: {host_ip} — {len(vms)} VMs")
        return {"status": "success", "host": host_ip, "vms": len(vms)}

    except Exception as e:
        logger.error(f"[Host] Error on {host_ip}: {e}")
        return {"status": "error", "host": host_ip, "error": str(e)}


@shared_task(name="tasks.csv_scanner.fetch_cluster_csv_storage")
def fetch_cluster_csv_storage(cluster_id: int, cluster_name: str, nodes: list):
    """Scan CSV volumes for a cluster (tries each node until one succeeds)."""
    import os
    logger.info(f"[CSV] Scanning cluster {cluster_name}")

    if not nodes:
        return {"status": "error", "cluster": cluster_name, "message": "no nodes"}

    cache_seconds = int(os.getenv("CSV_CACHE_SECONDS", "3600"))
    if not _should_rescan_csv(cluster_id, cache_seconds):
        return {"status": "skipped", "cluster": cluster_name}

    for node in nodes:
        try:
            session = create_winrm_session(node, cluster_id=cluster_id)
            csv_list = collect_csv_volumes(session)
            save_csv_volumes(csv_list, cluster_name)
            logger.info(f"[CSV] {cluster_name}: {len(csv_list)} volumes from {node}")
            _update_csv_scan_metadata(cluster_id, "success", vol_count=len(csv_list))
            return {"status": "success", "cluster": cluster_name, "volumes": len(csv_list), "node": node}
        except Exception as e:
            logger.warning(f"[CSV] Node {node} failed: {e}")
            continue

    error_msg = f"All nodes failed for cluster {cluster_name}"
    logger.error(error_msg)
    _update_csv_scan_metadata(cluster_id, "error", error=error_msg)
    return {"status": "error", "cluster": cluster_name, "message": error_msg}


@shared_task(name="tasks.sync.aggregate_sync_results_with_csv")
def aggregate_and_monitor(results, vm_snapshot=None, csv_snapshot=None):
    """Chord callback: aggregate results, run change detection, dispatch notifications."""
    logger.info(f"Aggregating {len(results)} task results")

    total_vms = sum(r.get("vms", 0) for r in results if isinstance(r, dict) and r.get("status") == "success")
    errors = [r.get("error") for r in results if isinstance(r, dict) and r.get("status") == "error"]

    # Change detection
    if vm_snapshot is not None:
        threshold = get_storage_threshold()
        vm_events = detect_vm_changes(vm_snapshot)
        storage_events = detect_storage_changes(csv_snapshot or {}, threshold_pct=threshold)
        all_events = vm_events + storage_events

        if all_events:
            persist_events(all_events)
            dispatch_notifications(all_events)
            logger.info(f"Detected {len(all_events)} changes ({len(vm_events)} VM, {len(storage_events)} storage)")

    status = "success" if not errors else "partial"
    _update_sync_metadata(status, vms_synced=total_vms, errors="; ".join(errors) if errors else None)
    logger.info(f"Sync complete: {total_vms} VMs, status={status}")
    return {"status": status, "vms_synced": total_vms}


def _should_rescan_csv(cluster_id: int, cache_seconds: int) -> bool:
    try:
        from datetime import datetime
        conn = get_db_connection()
        row = conn.execute(
            "SELECT last_scan_end FROM csv_scan_metadata WHERE cluster_id = ?", (cluster_id,)
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return True
        last = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - last).total_seconds() > cache_seconds
    except Exception:
        return True


def _update_csv_scan_metadata(cluster_id: int, status: str, vol_count: int = 0, error: str = None):
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db_connection()
        conn.execute("""
            INSERT OR REPLACE INTO csv_scan_metadata
            (cluster_id, last_scan_end, scan_status, volumes_scanned, errors)
            VALUES (?, ?, ?, ?, ?)
        """, (cluster_id, now, status, vol_count, error))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"csv_scan_metadata update failed: {e}")
```

**Step 2: Commit** (orchestrator is integration-tested by running a real sync)

```bash
git add tasks/orchestrator.py
git commit -m "feat: add tasks/orchestrator.py (replaces sync.py + csv_scanner.py)"
```

---

## Task 10: Settings route + notification config UI

**Files:**
- Modify: `app/routes/settings.py` — add notification settings GET/POST

**Step 1: Add settings routes to `app/routes/settings.py`**

Find the end of `app/routes/settings.py` and add:

```python
@bp.route("/settings/notifications", methods=["GET", "POST"])
@login_required
def notification_settings():
    """View and update notification settings."""
    with get_db() as db:
        if request.method == "POST":
            fields = [
                "storage_threshold_pct", "enable_email_alerts", "enable_webhook_alerts",
                "webhook_url", "alert_email_to", "smtp_host", "smtp_port",
                "smtp_user", "smtp_password",
            ]
            from datetime import datetime
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for field in fields:
                value = request.form.get(field, "")
                db.execute("""
                    INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """, (field, value, now))
            db.commit()
            from flask import flash
            flash("Notification settings saved.", "success")
            return redirect("/settings/notifications")

        # GET: load all settings
        rows = db.execute("SELECT key, value FROM settings").fetchall()
        settings = {r["key"]: r["value"] for r in rows}
        return render_template("notification_settings.html", settings=settings)
```

**Step 2: Create minimal template `templates/notification_settings.html`**

```html
{% extends "base.html" %}
{% block title %}Notification Settings{% endblock %}
{% block content %}
<div class="container py-4">
  <h2>Notification Settings</h2>
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% for cat, msg in messages %}<div class="alert alert-{{ cat }}">{{ msg }}</div>{% endfor %}
  {% endwith %}
  <form method="POST">
    <div class="card mb-3">
      <div class="card-header">Storage Alerts</div>
      <div class="card-body">
        <label class="form-label">Low storage threshold (%)</label>
        <input type="number" name="storage_threshold_pct" class="form-control"
               value="{{ settings.get('storage_threshold_pct', '20') }}" min="1" max="99">
        <div class="form-text">Alert when CSV free space drops below this percentage.</div>
      </div>
    </div>
    <div class="card mb-3">
      <div class="card-header">Email Alerts</div>
      <div class="card-body">
        <div class="form-check mb-2">
          <input class="form-check-input" type="checkbox" name="enable_email_alerts" value="1"
                 id="emailCheck" {% if settings.get('enable_email_alerts') == '1' %}checked{% endif %}>
          <label class="form-check-label" for="emailCheck">Enable email alerts</label>
        </div>
        <input type="text" name="smtp_host" placeholder="SMTP Host" class="form-control mb-2"
               value="{{ settings.get('smtp_host', '') }}">
        <input type="number" name="smtp_port" placeholder="SMTP Port (587)" class="form-control mb-2"
               value="{{ settings.get('smtp_port', '587') }}">
        <input type="text" name="smtp_user" placeholder="SMTP Username" class="form-control mb-2"
               value="{{ settings.get('smtp_user', '') }}">
        <input type="password" name="smtp_password" placeholder="SMTP Password" class="form-control mb-2"
               value="{{ settings.get('smtp_password', '') }}">
        <input type="email" name="alert_email_to" placeholder="Send alerts to" class="form-control"
               value="{{ settings.get('alert_email_to', '') }}">
      </div>
    </div>
    <div class="card mb-3">
      <div class="card-header">Webhook (Slack / Teams / Generic)</div>
      <div class="card-body">
        <div class="form-check mb-2">
          <input class="form-check-input" type="checkbox" name="enable_webhook_alerts" value="1"
                 id="whCheck" {% if settings.get('enable_webhook_alerts') == '1' %}checked{% endif %}>
          <label class="form-check-label" for="whCheck">Enable webhook alerts</label>
        </div>
        <input type="url" name="webhook_url" placeholder="https://hooks.slack.com/..." class="form-control"
               value="{{ settings.get('webhook_url', '') }}">
      </div>
    </div>
    <button type="submit" class="btn btn-primary">Save Settings</button>
    <a href="/settings" class="btn btn-secondary ms-2">Back</a>
  </form>
</div>
{% endblock %}
```

**Step 3: Commit**

```bash
git add app/routes/settings.py templates/notification_settings.html
git commit -m "feat: add notification settings page (email/webhook/threshold config)"
```

---

## Task 11: Update celeryconfig and wire new modules

**Files:**
- Modify: `celeryconfig.py`
- Modify: `app/__init__.py` (add notifications settings link)

**Step 1: Update `celeryconfig.py`** to include new task modules

Replace the `include` list:

```python
celery.conf.update(
    broker_url="redis://localhost:6379/0",
    result_backend="redis://localhost:6379/0",
    include=["tasks.orchestrator"],   # single entry — all tasks registered here
    task_routes={
        "tasks.sync.fetch_hyperv_data": {"queue": "hyperv"},
        "tasks.sync.fetch_single_host": {"queue": "hyperv"},
        "tasks.sync.aggregate_sync_results_with_csv": {"queue": "hyperv"},
        "tasks.csv_scanner.fetch_cluster_csv_storage": {"queue": "csv"},
    },
)
```

**Step 2: Commit**

```bash
git add celeryconfig.py
git commit -m "chore: update celeryconfig to use tasks.orchestrator"
```

---

## Task 12: Remove old files (cleanup)

**Step 1: Archive old task files** — replace them with re-exports so nothing breaks if an old import exists somewhere.

Update `tasks/sync.py`:
```python
# Replaced by tasks/orchestrator.py — re-export for backward compatibility
from tasks.orchestrator import fetch_hyperv_data, fetch_single_host, aggregate_and_monitor  # noqa
```

Update `tasks/csv_scanner.py`:
```python
# Replaced by tasks/collectors/storage.py + tasks/persistence/storage.py
from tasks.orchestrator import fetch_cluster_csv_storage  # noqa
```

Update `tasks/hyperv.py`:
```python
# Replaced by tasks/collectors/winrm.py
from tasks.collectors.winrm import create_winrm_session, run_ps as run_powershell, run_ps_long as run_powershell_long  # noqa
```

**Step 2: Commit**

```bash
git add tasks/sync.py tasks/csv_scanner.py tasks/hyperv.py
git commit -m "chore: replace old task files with backward-compat re-exports"
```

---

## Task 13: Run full test suite and verify

**Step 1: Run all tests**

```bash
cd /opt/hyperv_inventory && source venv/bin/activate
pytest tests/ -v --ignore=tests/__pycache__
```

Expected: All tests PASS (no failures).

**Step 2: Verify Flask app starts**

```bash
cd /opt/hyperv_inventory && source venv/bin/activate
python -c "from app import create_app; app = create_app(); print('OK')"
```
Expected output: `OK`

**Step 3: Verify Celery task registration**

```bash
cd /opt/hyperv_inventory && source venv/bin/activate
celery -A tasks.orchestrator inspect registered 2>/dev/null || \
python -c "
from tasks.orchestrator import fetch_hyperv_data, fetch_single_host, fetch_cluster_csv_storage, aggregate_and_monitor
print('Tasks registered:')
for t in [fetch_hyperv_data, fetch_single_host, fetch_cluster_csv_storage, aggregate_and_monitor]:
    print(f'  {t.name}')
"
```

Expected: 4 task names printed.

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete hyperv inventory rewrite

- Clean 3-layer architecture: collectors / persistence / monitor
- Physical disk collection (Get-PhysicalDisk per host)
- Fixed CSV storage PS script (correct partition path, no double-encoding)
- Working change detection: VM state/CPU/RAM/IP/disk + storage low
- In-app + email + webhook notification delivery
- Notification settings UI (threshold, SMTP, webhook URL)
- Get-CimInstance replacing deprecated Get-WmiObject"
```

---

## Summary of new files

| New file | Purpose |
|----------|---------|
| `app/db/__init__.py` | DB factory + init_db with all tables |
| `app/db/migrations.py` | Idempotent schema migrations |
| `tasks/collectors/winrm.py` | WinRM session + PS execution |
| `tasks/collectors/vms.py` | VM/disk/network/snapshot/replication PS + collectors |
| `tasks/collectors/hosts.py` | Host info + physical disks + cluster nodes PS + collectors |
| `tasks/collectors/storage.py` | Fixed CSV PS script + collector |
| `tasks/persistence/vms.py` | save_vms/disks/networks/snapshots/replication |
| `tasks/persistence/hosts.py` | save_host + save_physical_disks |
| `tasks/persistence/storage.py` | save_csv_volumes |
| `tasks/monitor.py` | Snapshot + detect_vm_changes + detect_storage_changes + persist_events |
| `tasks/orchestrator.py` | Celery tasks (replaces sync.py) |
| `notifications/__init__.py` | dispatch_notifications |
| `notifications/email.py` | SMTP email delivery |
| `notifications/webhook.py` | HTTP webhook delivery |
| `templates/notification_settings.html` | Settings UI page |

## Test files

| Test file | Covers |
|-----------|--------|
| `tests/test_db_layer.py` | DB init, migrations, get_db context manager |
| `tests/test_winrm_collector.py` | run_ps, run_ps_long, error handling |
| `tests/test_vm_collectors.py` | collect_vms, collect_networks, PS script content |
| `tests/test_storage_collector.py` | collect_csv_volumes, fixed PS script |
| `tests/test_persistence.py` | save_vms, save_host, save_physical_disks, save_csv_volumes |
| `tests/test_monitor.py` | detect_vm_changes, detect_storage_changes, persist_events |
| `tests/test_notifications.py` | dispatch_notifications, webhook, email routing |
