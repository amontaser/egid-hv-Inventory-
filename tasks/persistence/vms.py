"""Persist VM-related data to SQLite."""

import logging
from datetime import datetime
from typing import List, Dict, Optional

from app.db import get_db_connection

logger = logging.getLogger(__name__)


def save_vms(vms: List[Dict], host_name: str, cluster_name: Optional[str] = None):
    if not vms:
        return
    conn = get_db_connection()
    c = conn.cursor()
    for vm in vms:
        effective_cluster = (
            cluster_name if cluster_name is not None else vm.get("ClusterName")
        )
        effective_host = vm.get("ComputerName") or host_name
        c.execute(
            """
            INSERT OR REPLACE INTO vm_info (
                vm_id, machine_name, host_name, cluster_name, state,
                uptime_seconds, cpu_count, memory_assigned_gb, memory_demand_gb,
                memory_startup_gb, memory_minimum_gb, memory_maximum_gb,
                dynamic_memory_enabled, generation, version, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            (
                vm.get("VMId"),
                vm.get("Name"),
                effective_host,
                effective_cluster,
                vm.get("State"),
                vm.get("UptimeSeconds", 0),
                vm.get("CPUCount", 0),
                vm.get("MemoryAssigned", 0),
                vm.get("MemoryDemand", 0),
                vm.get("MemoryStartup", 0),
                vm.get("MemoryMinimum", 0),
                vm.get("MemoryMaximum", 0),
                1 if vm.get("DynamicMemory") else 0,
                vm.get("Generation", 0),
                vm.get("Version"),
                vm.get("CreatedTime"),
            ),
        )
    conn.commit()
    conn.close()
    logger.info(f"Saved {len(vms)} VMs for {host_name}")


def save_disks(disks: List[Dict]):
    if not disks:
        return
    conn = get_db_connection()
    c = conn.cursor()
    for d in disks:
        c.execute(
            """
            INSERT OR REPLACE INTO vm_disks
            (vm_id, disk_name, disk_path, disk_format, size_gb, controller_type, controller_number, controller_location)
            VALUES (?,?,?,?,?,?,?,?)
        """,
            (
                d.get("VMId"),
                d.get("DiskName"),
                d.get("DiskPath"),
                d.get("DiskFormat"),
                d.get("Size", 0),
                d.get("ControllerType"),
                d.get("ControllerNumber"),
                d.get("ControllerLocation"),
            ),
        )
    conn.commit()
    conn.close()
    logger.info(f"Saved {len(disks)} VM disks")


def save_networks(networks: List[Dict]):
    if not networks:
        return
    conn = get_db_connection()
    c = conn.cursor()
    for n in networks:
        c.execute(
            """
            INSERT OR REPLACE INTO vm_network_adapters
            (vm_id, adapter_name, switch_name, mac_address, ip_addresses, is_connected, vlan_id, bandwidth_setting)
            VALUES (?,?,?,?,?,?,?,?)
        """,
            (
                n.get("VMId"),
                n.get("AdapterName"),
                n.get("SwitchName"),
                n.get("MacAddress"),
                n.get("IPAddresses"),
                1 if n.get("IsConnected") else 0,
                n.get("VlanId", 0),
                n.get("BandwidthMode"),
            ),
        )
    conn.commit()
    conn.close()


def save_snapshots(snapshots: List[Dict]):
    if not snapshots:
        return
    conn = get_db_connection()
    c = conn.cursor()
    for s in snapshots:
        c.execute(
            """
            INSERT OR REPLACE INTO vm_snapshots
            (vm_id, snapshot_name, snapshot_type, creation_time, parent_snapshot_id)
            VALUES (?,?,?,?,?)
        """,
            (
                s.get("VMId"),
                s.get("SnapshotName"),
                s.get("SnapshotType"),
                s.get("CreationTime"),
                s.get("ParentSnapshotName"),
            ),
        )
    conn.commit()
    conn.close()


def save_replication(reps: List[Dict]):
    if not reps:
        return
    conn = get_db_connection()
    c = conn.cursor()
    for r in reps:
        c.execute(
            """
            INSERT OR REPLACE INTO vm_replication
            (vm_id, replication_state, replication_health, replication_mode,
             primary_server, replica_server, frequency_seconds, last_replication_time)
            VALUES (?,?,?,?,?,?,?,?)
        """,
            (
                r.get("VMId"),
                r.get("ReplicationState"),
                r.get("ReplicationHealth"),
                r.get("ReplicationMode"),
                r.get("PrimaryServer"),
                r.get("ReplicaServer"),
                r.get("FrequencySeconds"),
                r.get("LastReplicationTime"),
            ),
        )
    conn.commit()
    conn.close()
