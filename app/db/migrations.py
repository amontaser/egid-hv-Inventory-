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
    _migrate_vm_unique_composite_key(db)
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
        cols = {
            r[1]
            for r in db.execute("PRAGMA table_info(cluster_shared_volumes)").fetchall()
        }
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


def _migrate_vm_unique_composite_key(db):
    """Change vm_info UNIQUE from (vm_id) to (vm_id, cluster_name) and add cluster_name to sub-tables."""
    try:
        info_cols = {r[1] for r in db.execute("PRAGMA table_info(vm_info)").fetchall()}

        if _has_composite_unique(db, "vm_info"):
            _add_cluster_name_to_subtables(db)
            return

        logger.info("Migrating vm_info to UNIQUE(vm_id, cluster_name)")

        db.execute("ALTER TABLE vm_info RENAME TO _vm_info_old")
        db.execute("""
            CREATE TABLE vm_info (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_name TEXT NOT NULL,
                vm_id TEXT NOT NULL,
                cluster_name TEXT NOT NULL DEFAULT 'Unknown',
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
                updated_at TEXT,
                UNIQUE(vm_id, cluster_name)
            )
        """)
        old_cols = ", ".join(c for c in info_cols if c != "id")
        db.execute(
            f"INSERT INTO vm_info (id, {old_cols}) SELECT id, {old_cols} FROM _vm_info_old"
        )
        db.execute("DROP TABLE _vm_info_old")

        _add_cluster_name_to_subtables(db)
        logger.info("vm_info migration complete")
    except Exception as e:
        logger.warning(f"vm_info composite key migration: {e}")


def _has_composite_unique(db, table):
    try:
        cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if "cluster_name" not in cols:
            return False
        indexes = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,)
        ).fetchall()
        for idx in indexes:
            if idx[0] and "vm_id" in idx[0] and "cluster_name" in idx[0]:
                return True
        unique_cols = set()
        for idx in indexes:
            if idx[0] and "UNIQUE" in idx[0].upper():
                if "vm_id" in idx[0] and "cluster_name" in idx[0]:
                    return True
        return False
    except Exception:
        return False


def _add_cluster_name_to_subtables(db):
    subtables = [
        "vm_disks",
        "vm_network_adapters",
        "vm_snapshots",
        "vm_replication",
        "vm_clients",
        "vm_notes",
    ]
    for table in subtables:
        _add_column(db, table, "cluster_name", "TEXT")
