"""Test that migration stub doesn't error."""

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


def test_run_migrations_noop(app_ctx):
    from app.db.migrations import run_migrations
    from app.db import get_db_connection

    session = get_db_connection()
    run_migrations(session)


def test_migrations_idempotent(app_ctx):
    from app.db.migrations import run_migrations
    from app.db import get_db_connection

    session = get_db_connection()
    run_migrations(session)
    run_migrations(session)


def _legacy_vm_info_engine(tmp_path):
    """Build a database with the pre-guest-column vm_info schema."""
    from sqlalchemy import create_engine, text

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE vm_info ("
                "id INTEGER PRIMARY KEY, "
                "vm_id VARCHAR(255), "
                "cluster_name VARCHAR(255), "
                "machine_name VARCHAR(255))"
            )
        )
    return engine


def test_migration_adds_missing_guest_columns_to_existing_table(tmp_path):
    """Existing tables must gain guest_hostname/guest_fqdn (PostgreSQL case)."""
    from sqlalchemy import inspect, text
    from sqlalchemy.orm import Session

    from app.db.migrations import run_migrations

    engine = _legacy_vm_info_engine(tmp_path)

    with Session(engine) as session:
        run_migrations(session)

    columns = {c["name"] for c in inspect(engine).get_columns("vm_info")}
    assert "guest_hostname" in columns
    assert "guest_fqdn" in columns

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO vm_info (vm_id, cluster_name, machine_name, "
                "guest_hostname, guest_fqdn) VALUES "
                "('vm-1', 'CLUSTER', 'app-01', 'app-01', 'app-01.corp.local')"
            )
        )
        row = conn.execute(
            text("SELECT guest_hostname, guest_fqdn FROM vm_info WHERE vm_id='vm-1'")
        ).fetchone()
    assert row == ("app-01", "app-01.corp.local")


def test_migration_adding_guest_columns_is_idempotent(tmp_path):
    from sqlalchemy import inspect
    from sqlalchemy.orm import Session

    from app.db.migrations import run_migrations

    engine = _legacy_vm_info_engine(tmp_path)

    with Session(engine) as session:
        run_migrations(session)
        run_migrations(session)

    columns = {c["name"] for c in inspect(engine).get_columns("vm_info")}
    assert "guest_hostname" in columns
    assert "guest_fqdn" in columns


def test_migration_adds_cluster_role_columns_to_existing_table(tmp_path):
    """PostgreSQL databases created before the cluster-role feature."""
    from sqlalchemy import inspect
    from sqlalchemy.orm import Session

    from app.db.migrations import run_migrations

    engine = _legacy_vm_info_engine(tmp_path)

    with Session(engine) as session:
        run_migrations(session)

    columns = {c["name"] for c in inspect(engine).get_columns("vm_info")}
    assert "is_clustered" in columns
    assert "cluster_role_state" in columns
    assert "cluster_owner_node" in columns


def test_migration_added_cluster_columns_are_usable(tmp_path):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.db.migrations import run_migrations

    engine = _legacy_vm_info_engine(tmp_path)

    with Session(engine) as session:
        run_migrations(session)
        session.execute(
            text(
                "INSERT INTO vm_info (vm_id, cluster_name, machine_name, "
                "is_clustered, cluster_role_state, cluster_owner_node) VALUES "
                "('vm-1', 'CLUSTER', 'app-01', 1, 'Online', 'HV-Node01')"
            )
        )
        session.commit()
        row = session.execute(
            text(
                "SELECT is_clustered, cluster_role_state, cluster_owner_node "
                "FROM vm_info WHERE vm_id='vm-1'"
            )
        ).fetchone()
    assert row == (1, "Online", "HV-Node01")
