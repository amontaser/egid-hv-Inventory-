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
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    expected = {
        "vm_info",
        "vm_disks",
        "vm_network_adapters",
        "vm_snapshots",
        "vm_replication",
        "hyperv_hosts",
        "host_physical_disks",
        "sync_metadata",
        "cluster_shared_volumes",
        "notifications",
        "vm_history",
        "settings",
        "clusters",
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
        row = conn2.execute(
            "SELECT value FROM settings WHERE key='test_key'"
        ).fetchone()
        conn2.close()
        assert row[0] == "val"
