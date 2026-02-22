import sqlite3
import pytest
from unittest.mock import patch
import app.db as db_module
from tasks.persistence.vms import save_vms
from tasks.persistence.hosts import save_host, save_physical_disks
from tasks.persistence.storage import save_csv_volumes


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    with patch.object(db_module, "DATABASE_PATH", db_path):
        db_module.init_db()
        # Also patch persistence modules
        import tasks.persistence.vms as pv
        import tasks.persistence.hosts as ph
        import tasks.persistence.storage as ps

        with (
            patch.object(pv, "get_db_connection", db_module.get_db_connection),
            patch.object(ph, "get_db_connection", db_module.get_db_connection),
            patch.object(ps, "get_db_connection", db_module.get_db_connection),
        ):
            yield db_path


def test_save_vms_inserts_rows(db):
    vms = [
        {
            "VMId": "vm1",
            "Name": "prod-web",
            "State": "Running",
            "CPUCount": 4,
            "MemoryAssigned": 8.0,
        }
    ]
    save_vms(vms, "HOST-01", cluster_name="PROD")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT * FROM vm_info WHERE vm_id='vm1'").fetchone()
    conn.close()
    assert row is not None


def test_save_vms_uses_explicit_cluster_name(db):
    vms = [{"VMId": "vm2", "Name": "test", "State": "Off", "ClusterName": "WRONG"}]
    save_vms(vms, "HOST-02", cluster_name="CORRECT")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT cluster_name FROM vm_info WHERE vm_id='vm2'").fetchone()
    conn.close()
    assert row[0] == "CORRECT"


def test_save_host_upserts(db):
    info = {
        "HostName": "NODE1",
        "TotalMemoryGB": 128.0,
        "AvailableMemoryGB": 64.0,
        "LogicalProcessors": 32,
        "VMCount": 10,
        "OSVersion": "Windows Server 2022",
    }
    save_host(info, cluster_name="PROD", connection_ip="10.0.0.1")
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT connection_ip FROM hyperv_hosts WHERE host_name='NODE1'"
    ).fetchone()
    conn.close()
    assert row[0] == "10.0.0.1"


def test_save_physical_disks(db):
    disks = [
        {
            "FriendlyName": "SEAGATE ST1000",
            "SerialNumber": "SN001",
            "MediaType": "HDD",
            "SizeGB": 1000.0,
            "HealthStatus": "Healthy",
            "OperationalStatus": "Online",
            "BusType": "SAS",
            "PartitionStyle": "GPT",
        }
    ]
    save_physical_disks(disks, "NODE1")
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT * FROM host_physical_disks WHERE host_name='NODE1'"
    ).fetchone()
    conn.close()
    assert row is not None


def test_save_csv_volumes(db):
    vols = [
        {
            "Name": "Cluster Disk 1",
            "TotalSizeGB": 2000.0,
            "FreeSpaceGB": 500.0,
            "PercentUsed": 75.0,
        }
    ]
    save_csv_volumes(vols, "PROD")
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT cluster_name FROM cluster_shared_volumes WHERE name='Cluster Disk 1'"
    ).fetchone()
    conn.close()
    assert row[0] == "PROD"
