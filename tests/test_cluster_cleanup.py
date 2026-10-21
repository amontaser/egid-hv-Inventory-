"""Cluster-level cleanup of VMs that no longer exist.

Cleanup must run once per cluster (after every node was collected), never per
node: a per-node cleanup deletes the other nodes' disks/networks/snapshots.
"""

import pytest
from app import create_app
from app.models import db as _db
from tasks.persistence.vms import (
    save_disks,
    save_networks,
    save_vms,
)


@pytest.fixture
def app_ctx(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    app = create_app()
    with app.app_context():
        yield app


def _seed_two_hosts(cluster="CLUSTER-01"):
    save_vms(
        [{"VMId": "vm-a", "Name": "a", "State": "Running"}], "HOST-A", cluster_name=cluster
    )
    save_vms(
        [{"VMId": "vm-b", "Name": "b", "State": "Running"}], "HOST-B", cluster_name=cluster
    )
    save_vms(
        [{"VMId": "vm-ghost", "Name": "ghost", "State": "Running"}], "HOST-A", cluster_name=cluster
    )
    save_disks(
        [{"VMId": "vm-a", "DiskName": "d1", "DiskPath": "/a/d1", "Size": 10, "UsedGB": 1}],
        cluster_name=cluster,
    )
    save_disks(
        [{"VMId": "vm-b", "DiskName": "d2", "DiskPath": "/b/d2", "Size": 20, "UsedGB": 2}],
        cluster_name=cluster,
    )
    save_networks(
        [
            {
                "VMId": "vm-a",
                "AdapterName": "nic",
                "MacAddress": "00155D00000A",
                "IPAddresses": "10.0.0.1",
            }
        ],
        cluster_name=cluster,
    )


def test_delete_stale_cluster_data_removes_only_unseen_vms(app_ctx):
    from sqlalchemy import text

    from tasks.persistence.vms import delete_stale_cluster_data

    _seed_two_hosts()

    removed = delete_stale_cluster_data("CLUSTER-01", ["vm-a", "vm-b"])

    assert removed == 1
    rows = _db.session.execute(
        text("SELECT vm_id FROM vm_info ORDER BY vm_id")
    ).fetchall()
    assert [r[0] for r in rows] == ["vm-a", "vm-b"]

    # child rows of the surviving VMs from BOTH hosts must stay
    disks = _db.session.execute(
        text("SELECT vm_id FROM vm_disks ORDER BY vm_id")
    ).fetchall()
    assert [r[0] for r in disks] == ["vm-a", "vm-b"]

    adapters = _db.session.execute(
        text("SELECT vm_id FROM vm_network_adapters")
    ).fetchall()
    assert [r[0] for r in adapters] == ["vm-a"]


def test_delete_stale_cluster_data_is_a_noop_when_everything_is_seen(app_ctx):
    from sqlalchemy import text

    from tasks.persistence.vms import delete_stale_cluster_data

    _seed_two_hosts()

    removed = delete_stale_cluster_data("CLUSTER-01", ["vm-a", "vm-b", "vm-ghost"])

    assert removed == 0
    count = _db.session.execute(text("SELECT COUNT(*) FROM vm_info")).fetchone()[0]
    assert count == 3


def test_cleanup_after_sync_keeps_all_hosts_data(app_ctx):
    from sqlalchemy import text

    from tasks.orchestrator import _cleanup_clusters_after_sync

    _seed_two_hosts()

    removed = _cleanup_clusters_after_sync(
        [
            {
                "status": "success",
                "host": "10.0.0.1",
                "cluster_name": "CLUSTER-01",
                "host_name": "HOST-A",
                "vm_ids": ["vm-a"],
            },
            {
                "status": "success",
                "host": "10.0.0.2",
                "cluster_name": "CLUSTER-01",
                "host_name": "HOST-B",
                "vm_ids": ["vm-b"],
            },
        ]
    )

    assert removed == 1
    disks = _db.session.execute(
        text("SELECT vm_id FROM vm_disks ORDER BY vm_id")
    ).fetchall()
    assert [r[0] for r in disks] == ["vm-a", "vm-b"]


def test_cleanup_after_sync_is_skipped_when_a_host_failed(app_ctx):
    from sqlalchemy import text

    from tasks.orchestrator import _cleanup_clusters_after_sync

    _seed_two_hosts()

    removed = _cleanup_clusters_after_sync(
        [
            {
                "status": "success",
                "host": "10.0.0.1",
                "cluster_name": "CLUSTER-01",
                "host_name": "HOST-A",
                "vm_ids": ["vm-a"],
            },
            {
                "status": "error",
                "host": "10.0.0.2",
                "cluster_name": "CLUSTER-01",
                "error": "connection refused",
            },
        ]
    )

    assert removed == 0
    count = _db.session.execute(text("SELECT COUNT(*) FROM vm_info")).fetchone()[0]
    assert count == 3


def test_cleanup_after_sync_is_skipped_when_no_vms_were_collected(app_ctx):
    from sqlalchemy import text

    from tasks.orchestrator import _cleanup_clusters_after_sync

    _seed_two_hosts()

    removed = _cleanup_clusters_after_sync(
        [
            {
                "status": "success",
                "host": "10.0.0.1",
                "cluster_name": "CLUSTER-01",
                "host_name": "HOST-A",
                "vm_ids": [],
            }
        ]
    )

    assert removed == 0
    count = _db.session.execute(text("SELECT COUNT(*) FROM vm_info")).fetchone()[0]
    assert count == 3


def test_failed_host_result_keeps_the_cluster_name(app_ctx, monkeypatch):
    """A failed node must be attributable to its cluster, otherwise the
    cleanup would delete the VMs of the node that failed."""
    from sqlalchemy import text

    from tasks import orchestrator

    _db.session.execute(
        text(
            "INSERT INTO clusters (id, cluster_name, is_enabled) "
            "VALUES (1, 'CLUSTER-01', 1)"
        )
    )
    _db.session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(orchestrator, "create_winrm_session", boom)

    result = orchestrator._fetch_single_host_impl("10.0.0.1", cluster_id=1)

    assert result["status"] == "error"
    assert result["cluster_name"] == "CLUSTER-01"
