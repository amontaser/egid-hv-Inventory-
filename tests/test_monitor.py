import pytest
import os
from sqlalchemy import text
from app import create_app
from app.models import db as _db
from tasks.monitor import detect_vm_changes, detect_storage_changes, persist_events


@pytest.fixture
def app_ctx():
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    app = create_app()
    with app.app_context():
        yield app


def _insert_vm(vm_id, name, state, cpu, mem, cluster):
    _db.session.execute(
        text("""
            INSERT OR REPLACE INTO vm_info
            (vm_id, machine_name, state, cpu_count, memory_assigned_gb, cluster_name, host_name)
            VALUES (:vm_id, :name, :state, :cpu, :mem, :cluster, 'HOST-01')
        """),
        {
            "vm_id": vm_id,
            "name": name,
            "state": state,
            "cpu": cpu,
            "mem": mem,
            "cluster": cluster,
        },
    )
    _db.session.commit()


def _insert_csv(name, cluster, pct_used):
    _db.session.execute(
        text("""
            INSERT OR REPLACE INTO cluster_shared_volumes
            (name, cluster_name, percent_used, free_space_gb, total_size_gb)
            VALUES (:name, :cluster, :pct_used, :free_gb, 1000)
        """),
        {
            "name": name,
            "cluster": cluster,
            "pct_used": pct_used,
            "free_gb": 100 * (1 - pct_used / 100),
        },
    )
    _db.session.commit()


def test_detect_new_vm(app_ctx):
    _insert_vm("vm1", "prod-web", "Running", 4, 8.0, "PROD")
    old_snapshot = {}
    events = detect_vm_changes(old_snapshot)
    assert any(e["change_type"] == "created" and e["vm_id"] == "vm1" for e in events)


def test_detect_deleted_vm(app_ctx):
    old_snapshot = {
        "vm-gone||PROD": {
            "vm_id": "vm-gone",
            "name": "old-vm",
            "state": "Running",
            "cpu_count": 2,
            "memory_gb": 4.0,
            "cluster": "PROD",
            "ip": None,
            "disk_count": 1,
        }
    }
    events = detect_vm_changes(old_snapshot)
    assert any(
        e["change_type"] == "deleted" and e["vm_id"] == "vm-gone" for e in events
    )


def test_detect_state_change(app_ctx):
    _insert_vm("vm1", "prod-web", "Off", 4, 8.0, "PROD")
    old_snapshot = {
        "vm1||PROD": {
            "vm_id": "vm1",
            "name": "prod-web",
            "state": "Running",
            "cpu_count": 4,
            "memory_gb": 8.0,
            "cluster": "PROD",
            "ip": None,
            "disk_count": 1,
        }
    }
    events = detect_vm_changes(old_snapshot)
    state_events = [e for e in events if e["change_type"] == "state_change"]
    assert len(state_events) == 1
    assert state_events[0]["old_value"] == "Running"
    assert state_events[0]["new_value"] == "Off"


def test_detect_cpu_change(app_ctx):
    _insert_vm("vm1", "prod-web", "Running", 8, 8.0, "PROD")
    old_snapshot = {
        "vm1||PROD": {
            "vm_id": "vm1",
            "name": "prod-web",
            "state": "Running",
            "cpu_count": 4,
            "memory_gb": 8.0,
            "cluster": "PROD",
            "ip": None,
            "disk_count": 1,
        }
    }
    events = detect_vm_changes(old_snapshot)
    assert any(e["change_type"] == "cpu" for e in events)


def test_detect_storage_low(app_ctx):
    _insert_csv("Cluster Disk 1", "PROD", 85.0)
    events = detect_storage_changes({}, threshold_pct=20.0)
    assert len(events) == 1
    assert events[0]["change_type"] == "storage_low"
    assert "Cluster Disk 1" in events[0]["message"]


def test_detect_storage_ok(app_ctx):
    _insert_csv("Cluster Disk 1", "PROD", 50.0)
    events = detect_storage_changes({}, threshold_pct=20.0)
    assert len(events) == 0


def test_persist_events_writes_notification(app_ctx):
    events = [
        {
            "vm_id": "vm1",
            "machine_name": "prod-web",
            "change_type": "state_change",
            "field_name": "state",
            "old_value": "Running",
            "new_value": "Off",
            "cluster_name": "PROD",
            "severity": "warning",
            "message": "VM prod-web: Running → Off",
            "detected_at": "2026-02-22 12:00:00",
        }
    ]
    persist_events(events)
    rows = _db.session.execute(text("SELECT * FROM notifications")).fetchall()
    history = _db.session.execute(text("SELECT * FROM vm_history")).fetchall()
    assert len(rows) == 1
    assert len(history) == 1
