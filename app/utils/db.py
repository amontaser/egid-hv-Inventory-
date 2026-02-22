"""Database connection utilities"""

import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger(__name__)

DATABASE_PATH = "/opt/hyperv_inventory/database.db"


def get_db_connection():
    """Get a database connection."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db():
    """Context manager for database connections."""
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


def init_db():
    """Initialize database tables."""
    db = get_db_connection()
    db.executescript("""
        -- VM Information table
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

        -- VM Disks table
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

        -- VM Network Adapters table
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

        -- VM Snapshots table
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

        -- VM Replication table
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

        -- Hyper-V Hosts table
        CREATE TABLE IF NOT EXISTS hyperv_hosts (
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

        -- Sync metadata table
        CREATE TABLE IF NOT EXISTS sync_metadata (
            id INTEGER PRIMARY KEY,
            last_sync_start TEXT,
            last_sync_end TEXT,
            last_sync_status TEXT,
            vms_synced INTEGER,
            hosts_discovered INTEGER,
            hosts_processed INTEGER,
            current_host TEXT,
            errors TEXT
        );

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

        -- Clients table
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            website TEXT,
            country TEXT,
            description TEXT,
            state INTEGER DEFAULT 1
        );

        -- Client contacts table
        CREATE TABLE IF NOT EXISTS client_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            is_primary_contact INTEGER DEFAULT 0,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        );

        -- VM to Client mapping
        CREATE TABLE IF NOT EXISTS vm_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_id TEXT NOT NULL,
            client_id INTEGER NOT NULL,
            FOREIGN KEY (vm_id) REFERENCES vm_info(vm_id),
            FOREIGN KEY (client_id) REFERENCES clients(id)
        );

        -- VM History/Audit Log table
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

        -- In-app Notifications table
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_type TEXT NOT NULL,
            vm_id TEXT,
            machine_name TEXT NOT NULL,
            message TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            is_read INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (vm_id) REFERENCES vm_info(vm_id)
        );

        -- Account Managers table
        CREATE TABLE IF NOT EXISTS account_managers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            state INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        );

        -- Client-Account Managers junction table
        CREATE TABLE IF NOT EXISTS client_account_managers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            manager_id INTEGER NOT NULL,
            assigned_at TEXT,
            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
            FOREIGN KEY (manager_id) REFERENCES account_managers(id) ON DELETE CASCADE,
            UNIQUE(client_id, manager_id)
        );

        -- VM Notes table (CRUD)
        CREATE TABLE IF NOT EXISTS vm_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_id TEXT NOT NULL,
            note_text TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (vm_id) REFERENCES vm_info(vm_id) ON DELETE CASCADE
        );

        -- Client Notes table (CRUD)
        CREATE TABLE IF NOT EXISTS client_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            note_text TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
        );

        -- Create indexes for better query performance
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
    """)

    # Seed initial account managers if table is empty
    try:
        manager_count = db.execute(
            "SELECT COUNT(*) as count FROM account_managers"
        ).fetchone()[0]
        if manager_count == 0:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                """
                INSERT INTO account_managers (name, email, phone, state, created_at, updated_at) VALUES
                ('Ahmed Mohamed', 'ahmed.mohamed@company.com', '+1-555-0101', 1, ?, ?),
                ('Sara Ali', 'sara.ali@company.com', '+1-555-0102', 1, ?, ?),
                ('Omar Hassan', 'omar.hassan@company.com', '+1-555-0103', 1, ?, ?)
            """,
                (now, now, now, now, now, now),
            )
            logger.info("Seeded initial account managers")
    except Exception as e:
        logger.warning(f"Could not seed account managers: {e}")

    _run_migrations(db)
    db.commit()
    logger.info("Database initialized successfully")
