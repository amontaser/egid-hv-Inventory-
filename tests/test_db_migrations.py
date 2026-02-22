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
