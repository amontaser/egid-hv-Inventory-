import pytest
import os
from app import create_app
from app.models import db as _db
from tasks.persistence.vms import save_vms
from tasks.persistence.hosts import save_host, save_physical_disks
from tasks.persistence.storage import save_csv_volumes


@pytest.fixture
def app_ctx():
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    app = create_app()
    with app.app_context():
        yield app


def test_save_vms_inserts_rows(app_ctx):
    from sqlalchemy import text

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
    row = _db.session.execute(
        text("SELECT * FROM vm_info WHERE vm_id='vm1'")
    ).fetchone()
    assert row is not None


def test_save_vms_uses_explicit_cluster_name(app_ctx):
    from sqlalchemy import text

    vms = [{"VMId": "vm2", "Name": "test", "State": "Off", "ClusterName": "WRONG"}]
    save_vms(vms, "HOST-02", cluster_name="CORRECT")
    row = _db.session.execute(
        text("SELECT cluster_name FROM vm_info WHERE vm_id='vm2'")
    ).fetchone()
    assert row[0] == "CORRECT"


def test_save_vms_persists_cluster_role_fields(app_ctx):
    from sqlalchemy import text

    save_vms(
        [
            {
                "VMId": "vm3",
                "Name": "clustered-vm",
                "State": "Running",
                "IsClustered": True,
                "ClusterRoleState": "Online",
                "ClusterOwnerNode": "HV-Node01",
            }
        ],
        "HOST-01",
        cluster_name="PROD",
    )
    row = _db.session.execute(
        text(
            "SELECT is_clustered, cluster_role_state, cluster_owner_node "
            "FROM vm_info WHERE vm_id='vm3'"
        )
    ).fetchone()
    assert bool(row[0]) is True
    assert row[1] == "Online"
    assert row[2] == "HV-Node01"


def test_save_vms_updates_cluster_role_fields_on_upsert(app_ctx):
    from sqlalchemy import text

    save_vms(
        [
            {
                "VMId": "vm4",
                "Name": "moved-vm",
                "State": "Running",
                "IsClustered": True,
                "ClusterRoleState": "Online",
                "ClusterOwnerNode": "HV-Node01",
            }
        ],
        "HOST-01",
        cluster_name="PROD",
    )
    save_vms(
        [
            {
                "VMId": "vm4",
                "Name": "moved-vm",
                "State": "Running",
                "IsClustered": False,
                "ClusterRoleState": None,
                "ClusterOwnerNode": None,
            }
        ],
        "HOST-01",
        cluster_name="PROD",
    )
    row = _db.session.execute(
        text(
            "SELECT is_clustered, cluster_role_state, cluster_owner_node "
            "FROM vm_info WHERE vm_id='vm4'"
        )
    ).fetchone()
    assert bool(row[0]) is False
    assert row[1] is None
    assert row[2] is None


def test_save_vms_keeps_known_cluster_flag_when_lookup_failed(app_ctx):
    """An unresolved cluster-role lookup must not mark VMs as standalone."""
    from sqlalchemy import text

    save_vms(
        [
            {
                "VMId": "vm5",
                "Name": "known-vm",
                "State": "Running",
                "IsClustered": True,
                "ClusterRoleState": "Online",
            }
        ],
        "HOST-01",
        cluster_name="PROD",
    )
    save_vms(
        [
            {
                "VMId": "vm5",
                "Name": "known-vm",
                "State": "Running",
                "IsClustered": None,
                "ClusterRoleState": None,
            }
        ],
        "HOST-01",
        cluster_name="PROD",
    )
    row = _db.session.execute(
        text("SELECT is_clustered, cluster_role_state FROM vm_info WHERE vm_id='vm5'")
    ).fetchone()
    assert bool(row[0]) is True
    assert row[1] == "Online"


def test_save_host_upserts(app_ctx):
    from sqlalchemy import text

    info = {
        "HostName": "NODE1",
        "TotalMemoryGB": 128.0,
        "AvailableMemoryGB": 64.0,
        "LogicalProcessors": 32,
        "VMCount": 10,
        "OSVersion": "Windows Server 2022",
    }
    save_host(info, cluster_name="PROD", connection_ip="10.0.0.1")
    row = _db.session.execute(
        text("SELECT connection_ip FROM hyperv_hosts WHERE host_name='NODE1'")
    ).fetchone()
    assert row[0] == "10.0.0.1"


def test_save_physical_disks(app_ctx):
    from sqlalchemy import text

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
    row = _db.session.execute(
        text("SELECT * FROM host_physical_disks WHERE host_name='NODE1'")
    ).fetchone()
    assert row is not None


def test_save_csv_volumes(app_ctx):
    from sqlalchemy import text

    vols = [
        {
            "Name": "Cluster Disk 1",
            "TotalSizeGB": 2000.0,
            "FreeSpaceGB": 500.0,
            "PercentUsed": 75.0,
        }
    ]
    save_csv_volumes(vols, "PROD")
    row = _db.session.execute(
        text(
            "SELECT cluster_name FROM cluster_shared_volumes WHERE name='Cluster Disk 1'"
        )
    ).fetchone()
    assert row[0] == "PROD"
