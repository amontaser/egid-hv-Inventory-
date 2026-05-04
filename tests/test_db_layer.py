import pytest
import os
from app import create_app
from app.models import db as _db


@pytest.fixture
def app_ctx():
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    app = create_app()
    with app.app_context():
        yield app


def test_init_db_creates_all_tables(app_ctx):
    inspector = _db.inspect(_db.engine)
    tables = set(inspector.get_table_names())
    expected = {
        "vm_info",
        "vm_disks",
        "vm_network_adapters",
        "vm_snapshots",
        "vm_replication",
        "hyperv_hosts",
        "host_physical_disks",
        "sync_metadata",
        "csv_scan_metadata",
        "cluster_shared_volumes",
        "notifications",
        "vm_history",
        "settings",
        "clusters",
        "cluster_nodes",
        "users",
        "clients",
        "client_contacts",
        "vm_clients",
        "account_managers",
        "client_account_managers",
        "vm_notes",
        "client_notes",
    }
    assert expected.issubset(tables)


def test_init_db_is_idempotent(app_ctx):
    from app.db import init_db

    init_db()
    init_db()


def test_settings_defaults_seeded(app_ctx):
    from app.models import Setting

    keys = {s.key for s in _db.session.query(Setting).all()}
    assert "storage_threshold_pct" in keys
    assert "enable_email_alerts" in keys


def test_get_db_context_manager_commits(app_ctx):
    from sqlalchemy import text
    from app.db import get_db

    with get_db() as session:
        session.execute(
            text("INSERT INTO settings (key, value) VALUES ('test_key', 'val')")
        )
    row = _db.session.execute(
        text("SELECT value FROM settings WHERE key='test_key'")
    ).scalar()
    assert row == "val"
