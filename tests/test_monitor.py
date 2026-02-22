import sqlite3
import pytest
from unittest.mock import patch
import app.db as db_module
from tasks.monitor import detect_vm_changes, detect_storage_changes, persist_events
import tasks.monitor as monitor_module


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    with (
        patch.object(db_module, "DATABASE_PATH", db_path),
        patch.object(monitor_module, "get_db_connection", db_module.get_db_connection),
    ):
        db_module.init_db()
        yield db_path


def _insert_vm(db_path, vm_id, name, state, cpu, mem, cluster):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO vm_info
        (vm_id, machine_name, state, cpu_count, memory_assigned_gb, cluster_name, host_name)
        VALUES (?,?,?,?,?,?,?)
    """,
        (vm_id, name, state, cpu, mem, cluster, "HOST-01"),
    )
    conn.commit()
    conn.close()


def _insert_csv(db_path, name, cluster, pct_used):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO cluster_shared_volumes
        (name, cluster_name, percent_used, free_space_gb, total_size_gb)
        VALUES (?,?,?,?,?)
    """,
        (name, cluster, pct_used, 100 * (1 - pct_used / 100), 1000),
    )
    conn.commit()
    conn.close()


def test_detect_new_vm(db):
    _insert_vm(db, "vm1", "prod-web", "Running", 4, 8.0, "PROD")
    old_snapshot = {}  # empty — vm1 is new
    events = detect_vm_changes(old_snapshot)
    assert any(e["change_type"] == "created" and e["vm_id"] == "vm1" for e in events)


def test_detect_deleted_vm(db):
    # vm1 is in old snapshot but not in DB (already cleared before sync)
    old_snapshot = {
        "vm-gone": {
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


def test_detect_state_change(db):
    _insert_vm(db, "vm1", "prod-web", "Off", 4, 8.0, "PROD")
    old_snapshot = {
        "vm1": {
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


def test_detect_cpu_change(db):
    _insert_vm(db, "vm1", "prod-web", "Running", 8, 8.0, "PROD")
    old_snapshot = {
        "vm1": {
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
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT * FROM notifications").fetchall()
    history = conn.execute("SELECT * FROM vm_history").fetchall()
    conn.close()
    assert len(rows) == 1
    assert len(history) == 1
