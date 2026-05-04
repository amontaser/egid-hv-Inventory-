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
        CREATE INDEX IF NOT EXISTS idx_vm_history_one_time_events
            ON vm_history(vm_id, change_type)
            WHERE change_type IN ('discovered', 'created', 'deleted');
        CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(is_read);
        CREATE INDEX IF NOT EXISTS idx_notifications_date ON notifications(created_at);
        CREATE INDEX IF NOT EXISTS idx_host_disks_host ON host_physical_disks(host_name);

        CREATE TABLE IF NOT EXISTS cluster_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_name TEXT NOT NULL,
            node_name TEXT NOT NULL,
            node_state TEXT,
            ip_address TEXT,
            last_updated TEXT,
            UNIQUE(cluster_name, node_name)
        );
    """)


def _seed_defaults(db):
    try:
        count = db.execute("SELECT COUNT(*) FROM account_managers").fetchone()[0]
        if count == 0:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                """
                INSERT INTO account_managers (name, email, phone, state, created_at, updated_at)
                VALUES
                  ('Ahmed Mohamed', 'ahmed.mohamed@company.com', '+1-555-0101', 1, ?, ?),
                  ('Sara Ali', 'sara.ali@company.com', '+1-555-0102', 1, ?, ?),
                  ('Omar Hassan', 'omar.hassan@company.com', '+1-555-0103', 1, ?, ?)
            """,
                (now, now, now, now, now, now),
            )
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
