# PostgreSQL Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace SQLite with PostgreSQL using SQLAlchemy ORM, Flask-SQLAlchemy, Alembic, and psycopg2-binary.

**Architecture:** Flask-SQLAlchemy manages the engine/session. All 20 tables become SQLAlchemy models. Alembic handles migrations. All raw SQL with `?` placeholders and `sqlite3.Row` access is replaced with SQLAlchemy `text()` queries using `:param` named bindings (minimal disruption to complex queries) plus ORM where simple. A compatibility layer in `app/db/__init__.py` preserves the `get_db()` / `get_db_connection()` interface so callers work with minimal changes.

**Tech Stack:** Flask-SQLAlchemy 3.1+, SQLAlchemy 2.0, Alembic 1.13+, psycopg2-binary, PostgreSQL 16

---

## Key Decision: Hybrid Approach (text() + ORM)

Rather than rewriting every complex SQL query as ORM (huge risk), we keep complex queries as `text()` SQL with PostgreSQL-compatible syntax changes:
- `?` → `:param` named bindings
- `INSERT OR IGNORE/REPLACE` → `INSERT ... ON CONFLICT DO NOTHING/UPDATE`
- `sqlite3.Row["col"]` → `sqlalchemy.Row._mapping["col"]` (preserves dict-like access)
- `GROUP_CONCAT` → `STRING_AGG` (PostgreSQL equivalent)

Simple CRUD operations use ORM models where natural.

---

### Task 1: Add Dependencies

**Files:**
- Modify: `requirements.txt`

**Step 1: Add new packages to requirements.txt**

Append these lines to `requirements.txt`:

```
Flask-SQLAlchemy>=3.1.1
SQLAlchemy>=2.0.36
psycopg2-binary>=2.9.10
alembic>=1.14.0
```

**Step 2: Install dependencies**

Run: `pip install Flask-SQLAlchemy SQLAlchemy psycopg2-binary alembic`

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add Flask-SQLAlchemy, SQLAlchemy, psycopg2-binary, alembic"
```

---

### Task 2: Define SQLAlchemy Models

**Files:**
- Rewrite: `app/models.py`

**Step 1: Write SQLAlchemy models**

Replace `app/models.py` with all 20 tables as SQLAlchemy declarative models. Each model maps directly to the existing table schema. Use `__tablename__` matching existing table names. Use `Column` definitions matching existing SQLite types.

```python
"""SQLAlchemy ORM models for Hyper-V Inventory."""

from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class VMInfo(db.Model):
    __tablename__ = "vm_info"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    machine_name = db.Column(db.String(255), nullable=False)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    host_name = db.Column(db.String(255))
    state = db.Column(db.String(50))
    uptime_seconds = db.Column(db.Integer)
    cpu_count = db.Column(db.Integer)
    memory_assigned_gb = db.Column(db.Numeric(10, 2))
    memory_demand_gb = db.Column(db.Numeric(10, 2))
    memory_startup_gb = db.Column(db.Numeric(10, 2))
    memory_minimum_gb = db.Column(db.Numeric(10, 2))
    memory_maximum_gb = db.Column(db.Numeric(10, 2))
    dynamic_memory_enabled = db.Column(db.Boolean, default=False)
    generation = db.Column(db.Integer)
    version = db.Column(db.String(50))
    notes = db.Column(db.Text)
    created_at = db.Column(db.String(30))
    updated_at = db.Column(db.String(30))

    __table_args__ = (
        db.UniqueConstraint("vm_id", "cluster_name", name="uq_vm_info_vm_id_cluster"),
        db.Index("idx_vm_info_machine_name", "machine_name"),
        db.Index("idx_vm_info_host", "host_name"),
        db.Index("idx_vm_info_state", "state"),
        db.Index("idx_vm_info_cluster", "cluster_name"),
    )


class VMDisk(db.Model):
    __tablename__ = "vm_disks"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    disk_name = db.Column(db.String(255))
    disk_path = db.Column(db.Text)
    disk_format = db.Column(db.String(50))
    size_gb = db.Column(db.Numeric(10, 2))
    used_gb = db.Column(db.Numeric(10, 2))
    controller_type = db.Column(db.String(50))
    controller_number = db.Column(db.Integer)
    controller_location = db.Column(db.Integer)

    __table_args__ = (
        db.Index("idx_vm_disks_vm_id", "vm_id", "cluster_name"),
    )


class VMNetworkAdapter(db.Model):
    __tablename__ = "vm_network_adapters"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    adapter_name = db.Column(db.String(255))
    switch_name = db.Column(db.String(255))
    vlan_id = db.Column(db.Integer)
    mac_address = db.Column(db.String(17))
    ip_addresses = db.Column(db.Text)
    is_connected = db.Column(db.Boolean, default=False)
    bandwidth_setting = db.Column(db.String(100))

    __table_args__ = (
        db.Index("idx_vm_network_vm_id", "vm_id", "cluster_name"),
    )


class VMSnapshot(db.Model):
    __tablename__ = "vm_snapshots"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    snapshot_name = db.Column(db.String(255))
    snapshot_id = db.Column(db.String(255))
    snapshot_type = db.Column(db.String(50))
    creation_time = db.Column(db.String(30))
    parent_snapshot_id = db.Column(db.String(255))

    __table_args__ = (
        db.Index("idx_vm_snapshots_vm_id", "vm_id", "cluster_name"),
    )


class VMReplication(db.Model):
    __tablename__ = "vm_replication"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    replication_state = db.Column(db.String(50))
    replication_health = db.Column(db.String(50))
    replication_mode = db.Column(db.String(50))
    primary_server = db.Column(db.String(255))
    replica_server = db.Column(db.String(255))
    frequency_seconds = db.Column(db.Integer)
    last_replication_time = db.Column(db.String(30))


class HyperVHost(db.Model):
    __tablename__ = "hyperv_hosts"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    host_name = db.Column(db.String(255), unique=True)
    cluster_name = db.Column(db.String(255))
    total_memory_gb = db.Column(db.Numeric(10, 2))
    available_memory_gb = db.Column(db.Numeric(10, 2))
    logical_processors = db.Column(db.Integer)
    vm_count = db.Column(db.Integer)
    os_version = db.Column(db.String(255))
    hyperv_version = db.Column(db.String(255))
    vhd_default_path = db.Column(db.Text)
    vm_default_path = db.Column(db.Text)
    connection_ip = db.Column(db.String(45))
    last_updated = db.Column(db.String(30))


class HostPhysicalDisk(db.Model):
    __tablename__ = "host_physical_disks"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    host_name = db.Column(db.String(255), nullable=False)
    friendly_name = db.Column(db.String(255))
    serial_number = db.Column(db.String(255))
    media_type = db.Column(db.String(50))
    size_gb = db.Column(db.Numeric(10, 2))
    health_status = db.Column(db.String(50))
    operational_status = db.Column(db.String(50))
    bus_type = db.Column(db.String(50))
    partition_style = db.Column(db.String(50))
    disk_number = db.Column(db.Integer)
    last_updated = db.Column(db.String(30))

    __table_args__ = (
        db.UniqueConstraint("host_name", "serial_number", name="uq_host_disk_host_serial"),
        db.Index("idx_host_disks_host", "host_name"),
    )


class SyncMetadata(db.Model):
    __tablename__ = "sync_metadata"

    id = db.Column(db.Integer, primary_key=True, default=1)
    last_sync_start = db.Column(db.String(30))
    last_sync_end = db.Column(db.String(30))
    last_sync_status = db.Column(db.String(50))
    vms_synced = db.Column(db.Integer)
    hosts_discovered = db.Column(db.Integer)
    hosts_processed = db.Column(db.Integer)
    current_host = db.Column(db.String(255))
    current_cluster = db.Column(db.String(255))
    errors = db.Column(db.Text)


class CSVScanMetadata(db.Model):
    __tablename__ = "csv_scan_metadata"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    cluster_id = db.Column(db.Integer)
    last_scan_start = db.Column(db.String(30))
    last_scan_end = db.Column(db.String(30))
    scan_status = db.Column(db.String(50))
    volumes_scanned = db.Column(db.Integer)
    total_vhds_found = db.Column(db.Integer)
    scan_duration_seconds = db.Column(db.Numeric(10, 2))
    errors = db.Column(db.Text)


class ClusterSharedVolume(db.Model):
    __tablename__ = "cluster_shared_volumes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    volume_path = db.Column(db.Text)
    owner_node = db.Column(db.String(255))
    state = db.Column(db.String(50))
    total_size_gb = db.Column(db.Numeric(10, 2))
    free_space_gb = db.Column(db.Numeric(10, 2))
    used_space_gb = db.Column(db.Numeric(10, 2))
    percent_used = db.Column(db.Numeric(5, 2))
    maintenance_mode = db.Column(db.Boolean, default=False)
    redirected_access = db.Column(db.Boolean, default=False)
    vhd_count = db.Column(db.Integer, default=0)
    vhd_max_size_gb = db.Column(db.Numeric(10, 2), default=0)
    vhd_actual_size_gb = db.Column(db.Numeric(10, 2), default=0)
    oversubscription_percent = db.Column(db.Numeric(5, 2), default=0)
    oversubscription_gb = db.Column(db.Numeric(10, 2), default=0)
    cluster_name = db.Column(db.String(255))
    last_updated = db.Column(db.String(30))

    __table_args__ = (
        db.UniqueConstraint("name", "volume_path", "cluster_name", name="uq_csv_name_path_cluster"),
    )


class Cluster(db.Model):
    __tablename__ = "clusters"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    cluster_name = db.Column(db.String(255), nullable=False, unique=True)
    location = db.Column(db.String(255))
    is_enabled = db.Column(db.Boolean, default=True)
    domain = db.Column(db.String(255))
    cluster_name_for_ps = db.Column(db.String(255))
    domain_name = db.Column(db.String(255))
    dns_servers = db.Column(db.Text)
    username = db.Column(db.String(255))
    password = db.Column(db.Text)
    transport = db.Column(db.String(50), default="ntlm")
    require_https = db.Column(db.Boolean, default=False)


class ClusterNode(db.Model):
    __tablename__ = "cluster_nodes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    cluster_name = db.Column(db.String(255), nullable=False)
    node_name = db.Column(db.String(255), nullable=False)
    node_state = db.Column(db.String(50))
    ip_address = db.Column(db.String(45))
    last_updated = db.Column(db.String(30))

    __table_args__ = (
        db.UniqueConstraint("cluster_name", "node_name", name="uq_cluster_node"),
    )


class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    website = db.Column(db.String(500))
    country = db.Column(db.String(100))
    description = db.Column(db.Text)
    state = db.Column(db.Integer, default=1)


class ClientContact(db.Model):
    __tablename__ = "client_contacts"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255))
    phone = db.Column(db.String(50))
    is_primary_contact = db.Column(db.Boolean, default=False)


class VMClient(db.Model):
    __tablename__ = "vm_clients"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)


class VMHistory(db.Model):
    __tablename__ = "vm_history"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255))
    machine_name = db.Column(db.String(255), nullable=False)
    change_type = db.Column(db.String(50), nullable=False)
    field_name = db.Column(db.String(100))
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    change_description = db.Column(db.Text)
    detected_at = db.Column(db.String(30), nullable=False)

    __table_args__ = (
        db.Index("idx_vm_history_vm_id", "vm_id"),
        db.Index("idx_vm_history_date", "detected_at"),
        db.Index("idx_vm_history_type", "change_type"),
    )


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    notification_type = db.Column(db.String(50), nullable=False)
    vm_id = db.Column(db.String(255))
    machine_name = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(20), default="info")
    cluster_name = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.String(30), nullable=False)

    __table_args__ = (
        db.Index("idx_notifications_read", "is_read"),
        db.Index("idx_notifications_date", "created_at"),
    )


class AccountManager(db.Model):
    __tablename__ = "account_managers"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255))
    phone = db.Column(db.String(50))
    state = db.Column(db.Integer, default=1)
    created_at = db.Column(db.String(30))
    updated_at = db.Column(db.String(30))


class ClientAccountManager(db.Model):
    __tablename__ = "client_account_managers"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    manager_id = db.Column(db.Integer, db.ForeignKey("account_managers.id", ondelete="CASCADE"), nullable=False)
    assigned_at = db.Column(db.String(30))

    __table_args__ = (
        db.UniqueConstraint("client_id", "manager_id", name="uq_client_manager"),
    )


class VMNote(db.Model):
    __tablename__ = "vm_notes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    note_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.String(30))
    updated_at = db.Column(db.String(30))


class ClientNote(db.Model):
    __tablename__ = "client_notes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    note_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.String(30))
    updated_at = db.Column(db.String(30))


class Setting(db.Model):
    __tablename__ = "settings"

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text)
    updated_at = db.Column(db.String(30))
```

**Step 2: Verify models load**

Run: `python -c "from app.models import db, VMInfo; print('Models OK')"`

**Step 3: Commit**

```bash
git add app/models.py
git commit -m "feat: add SQLAlchemy ORM models for all tables"
```

---

### Task 3: Rewrite Database Layer

**Files:**
- Rewrite: `app/db/__init__.py`
- Rewrite: `app/db/migrations.py`
- Modify: `app/__init__.py`
- Modify: `app/utils/db.py`

**Step 1: Rewrite `app/db/__init__.py`**

Replace with Flask-SQLAlchemy integration. Provide `get_db()` and `get_db_connection()` compatibility that return `db.session` (the SQLAlchemy session). The `get_db()` context manager commits on success, rolls back on error — matching the current behavior.

```python
"""Database layer — SQLAlchemy engine, session factory, schema init."""

import logging
from contextlib import contextmanager
from datetime import datetime

from app.models import db

logger = logging.getLogger(__name__)


def configure_db(app):
    """Configure Flask-SQLAlchemy on the Flask app."""
    database_url = app.config.get("SQLALCHEMY_DATABASE_URI")
    if not database_url:
        database_url = "postgresql://hyperv:hyperv@localhost:5432/hyperv_inventory"
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
    }
    db.init_app(app)


def get_db_connection():
    """Return the current SQLAlchemy session (for outside Flask request context)."""
    return db.session


@contextmanager
def get_db():
    """Context manager yielding a SQLAlchemy session. Commits on success, rolls back on error."""
    session = db.session
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Database error: {e}")
        raise


def init_db():
    """Initialize all tables and seed defaults. Safe to call multiple times."""
    _seed_defaults()
    logger.info("Database initialized successfully")


def _seed_defaults():
    """Seed default account managers and settings."""
    session = db.session
    try:
        count = session.query(AccountManager).count()
        if count == 0:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for name, email, phone in [
                ("Ahmed Mohamed", "ahmed.mohamed@company.com", "+1-555-0101"),
                ("Sara Ali", "sara.ali@company.com", "+1-555-0102"),
                ("Omar Hassan", "omar.hassan@company.com", "+1-555-0103"),
            ]:
                session.add(AccountManager(name=name, email=email, phone=phone, state=1, created_at=now, updated_at=now))
            session.commit()
    except Exception as e:
        session.rollback()
        logger.warning(f"Could not seed account managers: {e}")

    defaults = [
        ("storage_threshold_pct", "20"),
        ("enable_email_alerts", "0"),
        ("enable_webhook_alerts", "0"),
        ("webhook_url", ""),
        ("alert_email_to", ""),
    ]
    for key, value in defaults:
        try:
            existing = session.query(Setting).filter_by(key=key).first()
            if not existing:
                session.add(Setting(key=key, value=value, updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        except Exception:
            pass
    try:
        session.commit()
    except Exception as e:
        session.rollback()
        logger.warning(f"Could not seed settings: {e}")


from app.models import AccountManager, Setting
```

**Step 2: Clear `app/db/migrations.py`**

Since Alembic handles migrations, replace with a stub:

```python
"""Schema migrations handled by Alembic. See alembic/ directory."""
import logging

logger = logging.getLogger(__name__)


def run_migrations(db):
    """Migrations are now handled by Alembic. This is a no-op stub."""
    logger.info("Migrations handled by Alembic (no-op)")
```

**Step 3: Update `app/__init__.py`**

Add `configure_db` call in `create_app()` and use `db.create_all()` for initial schema creation:

Change the database init section at the bottom of `create_app()` from:

```python
    # Initialize database
    from app.utils.db import init_db

    with app.app_context():
        init_db()
```

To:

```python
    # Initialize database
    from app.db import configure_db, init_db

    configure_db(app)

    with app.app_context():
        from app.models import db
        db.create_all()
        init_db()
```

**Step 4: Update `app/utils/db.py`**

```python
"""Backward-compatible re-export — logic now lives in app/db/."""

from app.db import get_db_connection, get_db, init_db  # noqa: F401
from app.db.migrations import run_migrations as _run_migrations  # noqa: F401
```

(This file stays the same — it already re-exports from the right place.)

**Step 5: Verify app starts**

Run: `python -c "from app import create_app; app = create_app(); print('App OK')"`

This will fail without a running PostgreSQL, but should not have import errors. Fix any import issues.

**Step 6: Commit**

```bash
git add app/db/__init__.py app/db/migrations.py app/__init__.py app/utils/db.py
git commit -m "feat: rewrite DB layer with Flask-SQLAlchemy and PostgreSQL support"
```

---

### Task 4: Update Docker Configuration

**Files:**
- Modify: `docker-compose.yml`
- Modify: `Dockerfile`

**Step 1: Add PostgreSQL service to `docker-compose.yml`**

Add after the `redis` service:

```yaml
  postgres:
    image: postgres:16-alpine
    container_name: hyperv-postgres
    environment:
      POSTGRES_DB: hyperv_inventory
      POSTGRES_USER: hyperv
      POSTGRES_PASSWORD: hyperv_secret_2024
      TZ: Africa/Cairo
    volumes:
      - hyperv-pg-data:/var/lib/postgresql/data
    restart: unless-stopped
    networks:
      - hyperv-nw
```

**Step 2: Update environment variables in all services**

Replace every `DATABASE_PATH` line in all services with:
```
      - DATABASE_URL=postgresql://hyperv:hyperv_secret_2024@postgres:5432/hyperv_inventory
```

Add `depends_on: - postgres` to `web`, `celery-worker`, and `celery-beat` services.

**Step 3: Add PostgreSQL volume**

Under `volumes:`, add:
```yaml
  hyperv-pg-data:
    driver: local
```

**Step 4: Update Dockerfile**

Replace the `DATABASE_PATH` env line with:
```
ENV DATABASE_URL=postgresql://hyperv:hyperv_secret_2024@postgres:5432/hyperv_inventory
```

Also add `libpq-dev` to the apt-get install line (needed if psycopg2-binary has issues):
```
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*
```

**Step 5: Commit**

```bash
git add docker-compose.yml Dockerfile
git commit -m "feat: add PostgreSQL service and update Docker configuration"
```

---

### Task 5: Update Persistence Layer

**Files:**
- Rewrite: `tasks/persistence/vms.py`
- Rewrite: `tasks/persistence/hosts.py`
- Rewrite: `tasks/persistence/storage.py`

These files use `get_db_connection()` directly with raw SQL. Convert them to use SQLAlchemy `text()` queries with `:param` named bindings and PostgreSQL-compatible SQL.

**Step 1: Rewrite `tasks/persistence/vms.py`**

Replace the SQLite-specific raw SQL with SQLAlchemy text() queries. Key changes:
- `INSERT OR REPLACE INTO vm_info` → `INSERT INTO vm_info ... ON CONFLICT (vm_id, cluster_name) DO UPDATE SET ...`
- `?` → `:param`
- `conn = get_db_connection()` / `conn.commit()` / `conn.close()` → `session = get_db_connection()` / `session.commit()` (no close needed)

```python
"""Persist VM-related data to PostgreSQL."""

import logging
from typing import List, Dict, Optional

from sqlalchemy import text
from app.db import get_db_connection

logger = logging.getLogger(__name__)


def save_vms(vms: List[Dict], host_name: str, cluster_name: Optional[str] = None):
    if not vms:
        return
    session = get_db_connection()
    for vm in vms:
        effective_cluster = (
            cluster_name
            if cluster_name is not None
            else vm.get("ClusterName", "Unknown")
        )
        effective_host = vm.get("ComputerName") or host_name
        session.execute(
            text("""
                INSERT INTO vm_info (
                    vm_id, cluster_name, machine_name, host_name, state,
                    uptime_seconds, cpu_count, memory_assigned_gb, memory_demand_gb,
                    memory_startup_gb, memory_minimum_gb, memory_maximum_gb,
                    dynamic_memory_enabled, generation, version, created_at
                ) VALUES (
                    :vm_id, :cluster_name, :machine_name, :host_name, :state,
                    :uptime_seconds, :cpu_count, :memory_assigned_gb, :memory_demand_gb,
                    :memory_startup_gb, :memory_minimum_gb, :memory_maximum_gb,
                    :dynamic_memory_enabled, :generation, :version, :created_at
                )
                ON CONFLICT (vm_id, cluster_name) DO UPDATE SET
                    machine_name = EXCLUDED.machine_name,
                    host_name = EXCLUDED.host_name,
                    state = EXCLUDED.state,
                    uptime_seconds = EXCLUDED.uptime_seconds,
                    cpu_count = EXCLUDED.cpu_count,
                    memory_assigned_gb = EXCLUDED.memory_assigned_gb,
                    memory_demand_gb = EXCLUDED.memory_demand_gb,
                    memory_startup_gb = EXCLUDED.memory_startup_gb,
                    memory_minimum_gb = EXCLUDED.memory_minimum_gb,
                    memory_maximum_gb = EXCLUDED.memory_maximum_gb,
                    dynamic_memory_enabled = EXCLUDED.dynamic_memory_enabled,
                    generation = EXCLUDED.generation,
                    version = EXCLUDED.version,
                    created_at = EXCLUDED.created_at
            """),
            {
                "vm_id": vm.get("VMId"),
                "cluster_name": effective_cluster,
                "machine_name": vm.get("Name"),
                "host_name": effective_host,
                "state": vm.get("State"),
                "uptime_seconds": vm.get("UptimeSeconds", 0),
                "cpu_count": vm.get("CPUCount", 0),
                "memory_assigned_gb": vm.get("MemoryAssigned", 0),
                "memory_demand_gb": vm.get("MemoryDemand", 0),
                "memory_startup_gb": vm.get("MemoryStartup", 0),
                "memory_minimum_gb": vm.get("MemoryMinimum", 0),
                "memory_maximum_gb": vm.get("MemoryMaximum", 0),
                "dynamic_memory_enabled": 1 if vm.get("DynamicMemory") else 0,
                "generation": vm.get("Generation", 0),
                "version": vm.get("Version"),
                "created_at": vm.get("CreatedTime"),
            },
        )
    session.commit()
    logger.info(f"Saved {len(vms)} VMs for {host_name}")


def save_disks(disks: List[Dict], cluster_name: str = "Unknown"):
    if not disks:
        return
    session = get_db_connection()
    for d in disks:
        session.execute(
            text("""
                INSERT INTO vm_disks
                (vm_id, cluster_name, disk_name, disk_path, disk_format, size_gb, controller_type, controller_number, controller_location)
                VALUES (:vm_id, :cluster_name, :disk_name, :disk_path, :disk_format, :size_gb, :controller_type, :controller_number, :controller_location)
                ON CONFLICT DO NOTHING
            """),
            {
                "vm_id": d.get("VMId"),
                "cluster_name": cluster_name,
                "disk_name": d.get("DiskName"),
                "disk_path": d.get("DiskPath"),
                "disk_format": d.get("DiskFormat"),
                "size_gb": d.get("Size", 0),
                "controller_type": d.get("ControllerType"),
                "controller_number": d.get("ControllerNumber"),
                "controller_location": d.get("ControllerLocation"),
            },
        )
    session.commit()
    logger.info(f"Saved {len(disks)} VM disks")


def save_networks(networks: List[Dict], cluster_name: str = "Unknown"):
    if not networks:
        return
    session = get_db_connection()
    for n in networks:
        session.execute(
            text("""
                INSERT INTO vm_network_adapters
                (vm_id, cluster_name, adapter_name, switch_name, mac_address, ip_addresses, is_connected, vlan_id, bandwidth_setting)
                VALUES (:vm_id, :cluster_name, :adapter_name, :switch_name, :mac_address, :ip_addresses, :is_connected, :vlan_id, :bandwidth_setting)
                ON CONFLICT DO NOTHING
            """),
            {
                "vm_id": n.get("VMId"),
                "cluster_name": cluster_name,
                "adapter_name": n.get("AdapterName"),
                "switch_name": n.get("SwitchName"),
                "mac_address": n.get("MacAddress"),
                "ip_addresses": n.get("IPAddresses"),
                "is_connected": 1 if n.get("IsConnected") else 0,
                "vlan_id": n.get("VlanId", 0),
                "bandwidth_setting": n.get("BandwidthMode"),
            },
        )
    session.commit()


def save_snapshots(snapshots: List[Dict], cluster_name: str = "Unknown"):
    if not snapshots:
        return
    session = get_db_connection()
    for s in snapshots:
        session.execute(
            text("""
                INSERT INTO vm_snapshots
                (vm_id, cluster_name, snapshot_name, snapshot_type, creation_time, parent_snapshot_id)
                VALUES (:vm_id, :cluster_name, :snapshot_name, :snapshot_type, :creation_time, :parent_snapshot_id)
                ON CONFLICT DO NOTHING
            """),
            {
                "vm_id": s.get("VMId"),
                "cluster_name": cluster_name,
                "snapshot_name": s.get("SnapshotName"),
                "snapshot_type": s.get("SnapshotType"),
                "creation_time": s.get("CreationTime"),
                "parent_snapshot_id": s.get("ParentSnapshotName"),
            },
        )
    session.commit()


def save_replication(reps: List[Dict], cluster_name: str = "Unknown"):
    if not reps:
        return
    session = get_db_connection()
    for r in reps:
        session.execute(
            text("""
                INSERT INTO vm_replication
                (vm_id, cluster_name, replication_state, replication_health, replication_mode,
                 primary_server, replica_server, frequency_seconds, last_replication_time)
                VALUES (:vm_id, :cluster_name, :replication_state, :replication_health, :replication_mode,
                        :primary_server, :replica_server, :frequency_seconds, :last_replication_time)
                ON CONFLICT DO NOTHING
            """),
            {
                "vm_id": r.get("VMId"),
                "cluster_name": cluster_name,
                "replication_state": r.get("ReplicationState"),
                "replication_health": r.get("ReplicationHealth"),
                "replication_mode": r.get("ReplicationMode"),
                "primary_server": r.get("PrimaryServer"),
                "replica_server": r.get("ReplicaServer"),
                "frequency_seconds": r.get("FrequencySeconds"),
                "last_replication_time": r.get("LastReplicationTime"),
            },
        )
    session.commit()
```

**Step 2: Rewrite `tasks/persistence/hosts.py`**

Key change: `INSERT OR REPLACE INTO host_physical_disks` → `INSERT INTO host_physical_disks ... ON CONFLICT DO NOTHING` (the table uses `UNIQUE(host_name, serial_number)`). The `hyperv_hosts` table already uses `ON CONFLICT(host_name) DO UPDATE` which is PostgreSQL-compatible.

Convert `?` to `:param`, remove `conn.cursor()` pattern, use `session.execute()` directly.

**Step 3: Rewrite `tasks/persistence/storage.py`**

Convert `INSERT OR REPLACE INTO cluster_shared_volumes` → `INSERT INTO cluster_shared_volumes ... ON CONFLICT (name, volume_path, cluster_name) DO UPDATE SET ...` with all columns in the UPDATE clause.

Convert `?` to `:param`.

**Step 4: Commit**

```bash
git add tasks/persistence/vms.py tasks/persistence/hosts.py tasks/persistence/storage.py
git commit -m "feat: convert persistence layer to PostgreSQL-compatible SQLAlchemy queries"
```

---

### Task 6: Update Monitor Module

**Files:**
- Rewrite: `tasks/monitor.py`

**Step 1: Convert `tasks/monitor.py`**

Key changes:
- `get_db_connection()` → returns session, no `.close()` needed
- `?` → `:param`
- `INSERT OR IGNORE INTO vm_history` → `INSERT INTO vm_history ... ON CONFLICT DO NOTHING`
- Row access `r["col"]` → `r._mapping["col"]` (SQLAlchemy `Row` objects)
- Remove all `conn.close()` calls

The `snapshot_vm_states()` and `snapshot_csv_states()` functions use `r["col"]` dict access on rows. With SQLAlchemy `text()` queries, rows support `row._mapping["col"]` or can be converted with `dict(row._mapping)`.

For sub-queries like `(SELECT ip_addresses FROM vm_network_adapters WHERE ...)`, these work as-is in PostgreSQL.

**Step 2: Commit**

```bash
git add tasks/monitor.py
git commit -m "feat: convert monitor module to PostgreSQL-compatible queries"
```

---

### Task 7: Update Orchestrator

**Files:**
- Rewrite: `tasks/orchestrator.py`

**Step 1: Convert `tasks/orchestrator.py`**

Key changes:
- All `conn = get_db_connection()` / `conn.close()` → `session = get_db_connection()` (no close)
- `?` → `:param`
- `INSERT OR REPLACE INTO sync_metadata` → `INSERT INTO sync_metadata ... ON CONFLICT (id) DO UPDATE SET ...`
- `INSERT OR REPLACE INTO cluster_nodes` → `INSERT INTO cluster_nodes ... ON CONFLICT (cluster_name, node_name) DO UPDATE SET ...`
- `INSERT OR REPLACE INTO csv_scan_metadata` → `INSERT INTO csv_scan_metadata ... ON CONFLICT (id) DO UPDATE SET ...` (note: `csv_scan_metadata` uses `id` as primary key but may insert with specific `cluster_id`; check if `cluster_id` is unique or if we need a different conflict target)
- Row access `row["col"]` → `row._mapping["col"]`
- `conn.execute(f"DELETE FROM {tbl}")` → `session.execute(text(f"DELETE FROM {tbl}"))` (dynamic table names are fine since values are hardcoded)
- Remove all `conn.close()` calls

For `_clear_vm_data()`, the f-string DELETE is fine with PostgreSQL since the table names are hardcoded strings.

**Step 2: Commit**

```bash
git add tasks/orchestrator.py
git commit -m "feat: convert orchestrator to PostgreSQL-compatible queries"
```

---

### Task 8: Update Routes (Part 1 — vms, api)

**Files:**
- Rewrite: `app/routes/vms.py`
- Rewrite: `app/routes/api.py`

**Step 1: Convert `app/routes/vms.py`**

Key changes:
- `from app.utils.db import get_db` → stays the same (re-export works)
- `with get_db() as db:` → stays the same (returns session now)
- All `?` → `:param` named bindings
- `db.execute(sql, params)` → `db.execute(text(sql), params)` (wrap SQL in `text()`)
- `row["col"]` → `row._mapping["col"]`
- `dict(row)` → `dict(row._mapping)`
- `GROUP_CONCAT(...)` → `STRING_AGG(..., ',')` (PostgreSQL equivalent)
- Add `from sqlalchemy import text` import

For the dashboard query in `index()`, the `GROUP_CONCAT` subqueries become `STRING_AGG(col, ',')`.

For `get_all_clusters()`, the `SUM(CASE WHEN ...)` syntax works as-is in PostgreSQL.

**Step 2: Convert `app/routes/api.py`**

Same pattern: `text()` wrapping, `:param` bindings, `row._mapping["col"]`, `dict(row._mapping)`.

The dynamic query building with f-strings (`query += " AND host_name = ?"`) becomes `query += " AND host_name = :host"` with named params.

**Step 3: Commit**

```bash
git add app/routes/vms.py app/routes/api.py
git commit -m "feat: convert vms and api routes to PostgreSQL-compatible queries"
```

---

### Task 9: Update Routes (Part 2 — clients, storage, hosts, settings, clusters, notifications)

**Files:**
- Rewrite: `app/routes/clients.py`
- Rewrite: `app/routes/storage.py`
- Rewrite: `app/routes/hosts.py`
- Rewrite: `app/routes/settings.py`
- Rewrite: `app/routes/clusters.py`
- Rewrite: `app/routes/notifications.py`

**Step 1: Convert each route file**

Same pattern for all:
- Add `from sqlalchemy import text`
- Wrap all SQL strings in `text()`
- `?` → `:param`
- `row["col"]` → `row._mapping["col"]`
- `dict(row)` → `dict(row._mapping)`
- `INSERT INTO settings ... ON CONFLICT(key) DO UPDATE SET ...` is already PostgreSQL-compatible syntax (the current SQLite code uses it in hosts.py)
- `cursor.lastrowid` → after `session.execute()`, get the inserted ID via `cursor = session.execute(...)` then check if we need to use `session.flush()` and query for the ID, OR use `RETURNING id` clause. Simplest: use `session.execute(text("INSERT ... RETURNING id"), params).scalar()` where we need the ID.

For `cursor = db.cursor()` / `cursor.execute()` / `cursor.lastrowid` pattern in clients.py and vms.py:
```python
# Old SQLite pattern:
cursor = db.cursor()
cursor.execute("INSERT INTO clients (...) VALUES (?, ?)", (...))
client_id = cursor.lastrowid

# New PostgreSQL pattern:
result = db.execute(text("INSERT INTO clients (...) VALUES (:name, ...) RETURNING id"), {...})
client_id = result.scalar()
```

For `GROUP_CONCAT` in clients.py client_list route → `STRING_AGG(DISTINCT na.vlan_id::text, ',')`.

For the `settings.py` notification settings `ON CONFLICT(key) DO UPDATE SET` — this is already PostgreSQL-compatible.

For `storage.py` `export_csv` route: the `GROUP_CONCAT` → `STRING_AGG`.

**Step 2: Commit**

```bash
git add app/routes/clients.py app/routes/storage.py app/routes/hosts.py app/routes/settings.py app/routes/clusters.py app/routes/notifications.py
git commit -m "feat: convert remaining route files to PostgreSQL-compatible queries"
```

---

### Task 10: Update WinRM Collector and Notifications

**Files:**
- Modify: `tasks/collectors/winrm.py`
- Modify: `notifications/__init__.py`

**Step 1: Convert `tasks/collectors/winrm.py`**

In `_load_cluster_credentials()`:
- `conn = get_db_connection()` → `session = get_db_connection()` 
- `conn.execute("SELECT ... WHERE id = ?", (cluster_id,))` → `session.execute(text("SELECT ... WHERE id = :cluster_id"), {"cluster_id": cluster_id})`
- `row["col"]` → `row._mapping["col"]` (or check if row is None first)
- Remove `conn.close()`
- Add `from sqlalchemy import text`

**Step 2: Convert `notifications/__init__.py`**

In `_load_settings()`:
- Same pattern: `text()`, `:param`, `row._mapping["col"]`, no `close()`

**Step 3: Commit**

```bash
git add tasks/collectors/winrm.py notifications/__init__.py
git commit -m "feat: convert winrm collector and notifications to PostgreSQL-compatible queries"
```

---

### Task 11: Update Tests

**Files:**
- Rewrite: `tests/test_db_layer.py`
- Rewrite: `tests/test_db_migrations.py`
- Rewrite: `tests/test_persistence.py`
- Modify: `tests/test_monitor.py`

**Step 1: Rewrite `tests/test_db_layer.py`**

Tests need to work with SQLAlchemy sessions instead of raw sqlite3. Use an in-memory SQLite for tests (SQLAlchemy supports this) or mock the session. The simplest approach:

- Create a Flask app with `SQLALCHEMY_DATABASE_URI` set to `sqlite:///:memory:`
- Use `db.create_all()` to create tables
- Test using the session

```python
import pytest
from app import create_app
from app.models import db as _db


@pytest.fixture
def app_ctx():
    """Create app with in-memory SQLite for testing."""
    import os
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    app = create_app()
    with app.app_context():
        _db.create_all()
        yield app


def test_init_db_creates_all_tables(app_ctx):
    inspector = _db.inspect(_db.engine)
    tables = set(inspector.get_table_names())
    expected = {
        "vm_info", "vm_disks", "vm_network_adapters", "vm_snapshots", "vm_replication",
        "hyperv_hosts", "host_physical_disks", "sync_metadata", "cluster_shared_volumes",
        "notifications", "vm_history", "settings", "clusters",
    }
    assert expected.issubset(tables)


def test_settings_defaults_seeded(app_ctx):
    from app.models import Setting
    keys = {s.key for s in _db.session.query(Setting).all()}
    assert "storage_threshold_pct" in keys
    assert "enable_email_alerts" in keys


def test_get_db_context_manager_commits(app_ctx):
    from sqlalchemy import text
    from app.db import get_db
    with get_db() as session:
        session.execute(text("INSERT INTO settings (key, value) VALUES ('test_key', 'val')"))
    row = _db.session.execute(text("SELECT value FROM settings WHERE key='test_key'")).scalar()
    assert row == "val"
```

**Step 2: Update `tests/test_db_migrations.py`**

Since migrations are now Alembic-based and the old migration functions are no-ops, simplify this test or skip it. The test can verify that `run_migrations` doesn't error.

**Step 3: Update `tests/test_persistence.py`**

Replace SQLite fixture with SQLAlchemy session fixture. Use `app_ctx` fixture similar to test_db_layer.py.

**Step 4: Update `tests/test_monitor.py`**

Update any `sqlite3.connect()` calls to use SQLAlchemy sessions.

**Step 5: Run tests**

Run: `pytest tests/ -v`

Fix any failures.

**Step 6: Commit**

```bash
git add tests/
git commit -m "test: update tests to use SQLAlchemy sessions"
```

---

### Task 12: Set Up Alembic

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/` (directory)
- Create: `alembic/script.py.mako`

**Step 1: Initialize Alembic**

Run: `cd /opt/hyperv_inventory && python -m alembic init alembic`

**Step 2: Configure `alembic.ini`**

Set `sqlalchemy.url` to use env var:
```ini
sqlalchemy.url = postgresql://hyperv:hyperv_secret_2024@postgres:5432/hyperv_inventory
```

**Step 3: Update `alembic/env.py`**

Import the Flask app and models so Alembic can auto-generate migrations:

```python
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.models import db
target_metadata = db.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

**Step 4: Generate initial migration**

Run: `alembic revision --autogenerate -m "initial schema"`

**Step 5: Commit**

```bash
git add alembic/ alembic.ini
git commit -m "feat: initialize Alembic for PostgreSQL schema migrations"
```

---

### Task 13: Final Verification and Cleanup

**Files:**
- Review all modified files
- Remove: `app/db/migrations.py` old content (already stubbed)

**Step 1: Search for remaining SQLite references**

Run: `grep -r "sqlite3" --include="*.py" .` — should only appear in test fixtures if using in-memory SQLite.

Run: `grep -r "DATABASE_PATH" --include="*.py" .` — should be gone.

Run: `grep -r '"\?"' --include="*.py" .` — should find no `?` placeholders in SQL.

**Step 2: Verify imports**

Ensure all files that use `text()` import it: `from sqlalchemy import text`

**Step 3: Run tests**

Run: `pytest tests/ -v`

**Step 4: Final commit**

```bash
git add -A
git commit -m "chore: finalize PostgreSQL migration, clean up SQLite references"
```

---

### Task 14: Add Flask-Login Authentication

**Files:**
- Modify: `requirements.txt` (add Flask-Login)
- Modify: `app/models.py` (add User model)
- Modify: `app/__init__.py` (init Flask-Login)
- Rewrite: `app/routes/auth.py` (Flask-Login login/logout)
- Modify: `app/routes/vms.py` (replace custom login_required)
- Modify: `app/routes/api.py` (replace custom login_required)
- Modify: `app/routes/clients.py` (replace custom login_required)
- Modify: `app/routes/storage.py` (replace custom login_required)
- Modify: `app/routes/hosts.py` (replace custom login_required)
- Modify: `app/routes/settings.py` (replace custom login_required)
- Modify: `app/routes/clusters.py` (replace custom login_required)
- Modify: `app/routes/notifications.py` (replace custom login_required)
- Modify: `app/db/__init__.py` (seed default admin user)
- Update: login template (add username field)

**Step 1: Add Flask-Login to requirements.txt**

Append:
```
Flask-Login>=0.6.3
```

**Step 2: Add User model to `app/models.py`**

```python
from flask_login import UserMixin

class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(255), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="admin")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.String(30))
    updated_at = db.Column(db.String(30))
```

**Step 3: Initialize Flask-Login in `app/__init__.py`**

After creating the app, add:

```python
from flask_login import LoginManager

login_manager = LoginManager()
login_manager.login_view = "auth.login"

def create_app():
    ...
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        return db.session.get(User, int(user_id))
    ...
```

**Step 4: Rewrite `app/routes/auth.py`**

```python
"""Authentication routes — Flask-Login with username + password."""

from flask import Blueprint, request, redirect, url_for, render_template, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        from app.models import User, db

        user = db.session.query(User).filter_by(username=username, is_active=True).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user, remember=True)
            next_page = request.args.get("next", "/")
            return redirect(next_page)
        else:
            return render_template("login.html", error="Invalid username or password")
    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
```

**Step 5: Replace all 8 custom `login_required` decorators**

In every route file (`vms.py`, `api.py`, `clients.py`, `storage.py`, `hosts.py`, `settings.py`, `clusters.py`, `notifications.py`):

1. **Remove** the local `login_required` function definition (the `def login_required(f):` decorator + `wraps` import)
2. **Add** `from flask_login import login_required` to imports
3. All `@login_required` usages stay the same — Flask-Login's decorator works identically
4. Remove `from functools import wraps` if no longer used in the file
5. Remove `from flask import ... session ...` if `session` is no longer used elsewhere in the file

For the JSON API auth check (returning 401 for JSON requests), Flask-Login's `@login_required` redirects to login page by default. To preserve the 401 JSON behavior, configure `login_manager.login_view = "auth.login"` (already done) and override the unauthorized handler:

```python
@login_manager.unauthorized_handler
def unauthorized():
    from flask import request, jsonify, redirect
    if request.is_json:
        return jsonify({"error": "Authentication required"}), 401
    return redirect("/login")
```

This means the per-decorator `if request.is_json: return jsonify(...)` checks can be removed since the global handler covers it.

**Step 6: Update login template**

The current `login.html` has just a password field. Add a username field:

```html
<!-- Add before the password field: -->
<div class="mb-3">
    <label for="username" class="form-label">Username</label>
    <input type="text" class="form-control" id="username" name="username" required autofocus>
</div>
```

**Step 7: Seed default admin user in `app/db/__init__.py`**

In `_seed_defaults()`, add:

```python
from app.models import User
from werkzeug.security import generate_password_hash

admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
count = session.query(User).count()
if count == 0:
    session.add(User(
        username="admin",
        password_hash=generate_password_hash(admin_password),
        role="admin",
        is_active=True,
        created_at=now,
        updated_at=now,
    ))
    session.commit()
```

**Step 8: Run tests**

Run: `pytest tests/ -v`

**Step 9: Commit**

```bash
git add requirements.txt app/models.py app/__init__.py app/routes/auth.py app/routes/vms.py app/routes/api.py app/routes/clients.py app/routes/storage.py app/routes/hosts.py app/routes/settings.py app/routes/clusters.py app/routes/notifications.py app/db/__init__.py templates/login.html
git commit -m "feat: replace session auth with Flask-Login and users table"
```
