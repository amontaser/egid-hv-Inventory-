"""Persist VM-related data to SQLite."""

import logging
from datetime import datetime
from typing import List, Dict, Optional

from sqlalchemy import text

from app.db import get_db_connection

logger = logging.getLogger(__name__)


def save_vms(vms: List[Dict], host_name: str, cluster_name: Optional[str] = None):
    if not vms:
        return
    session = get_db_connection()
    for vm in vms:
        effective_cluster = (
            cluster_name
            if cluster_name is not None
            else vm.get("ClusterName", "Unknown")
        )
        effective_host = vm.get("ComputerName") or host_name
        session.execute(
            text("""
            INSERT INTO vm_info (
                vm_id, cluster_name, machine_name, host_name, state,
                uptime_seconds, cpu_count, memory_assigned_gb, memory_demand_gb,
                memory_startup_gb, memory_minimum_gb, memory_maximum_gb,
                dynamic_memory_enabled, generation, version, created_at
            ) VALUES (
                :vm_id, :cluster_name, :machine_name, :host_name, :state,
                :uptime_seconds, :cpu_count, :memory_assigned_gb, :memory_demand_gb,
                :memory_startup_gb, :memory_minimum_gb, :memory_maximum_gb,
                :dynamic_memory_enabled, :generation, :version, :created_at
            )
            ON CONFLICT (vm_id, cluster_name) DO UPDATE SET
                machine_name           = EXCLUDED.machine_name,
                host_name              = EXCLUDED.host_name,
                state                  = EXCLUDED.state,
                uptime_seconds         = EXCLUDED.uptime_seconds,
                cpu_count              = EXCLUDED.cpu_count,
                memory_assigned_gb     = EXCLUDED.memory_assigned_gb,
                memory_demand_gb       = EXCLUDED.memory_demand_gb,
                memory_startup_gb      = EXCLUDED.memory_startup_gb,
                memory_minimum_gb      = EXCLUDED.memory_minimum_gb,
                memory_maximum_gb      = EXCLUDED.memory_maximum_gb,
                dynamic_memory_enabled = EXCLUDED.dynamic_memory_enabled,
                generation             = EXCLUDED.generation,
                version                = EXCLUDED.version,
                created_at             = EXCLUDED.created_at
        """),
            {
                "vm_id": vm.get("VMId"),
                "cluster_name": effective_cluster,
                "machine_name": vm.get("Name"),
                "host_name": effective_host,
                "state": vm.get("State"),
                "uptime_seconds": vm.get("UptimeSeconds", 0),
                "cpu_count": vm.get("CPUCount", 0),
                "memory_assigned_gb": vm.get("MemoryAssigned", 0),
                "memory_demand_gb": vm.get("MemoryDemand", 0),
                "memory_startup_gb": vm.get("MemoryStartup", 0),
                "memory_minimum_gb": vm.get("MemoryMinimum", 0),
                "memory_maximum_gb": vm.get("MemoryMaximum", 0),
                "dynamic_memory_enabled": bool(vm.get("DynamicMemory")),
                "generation": vm.get("Generation", 0),
                "version": vm.get("Version"),
                "created_at": vm.get("CreatedTime"),
            },
        )
    session.commit()
    logger.info(f"Saved {len(vms)} VMs for {host_name}")


def save_disks(disks: List[Dict], cluster_name: str = "Unknown"):
    if not disks:
        return
    session = get_db_connection()
    for d in disks:
        session.execute(
            text("""
            INSERT INTO vm_disks
            (vm_id, cluster_name, disk_name, disk_path, disk_format, size_gb, controller_type, controller_number, controller_location)
            VALUES (:vm_id, :cluster_name, :disk_name, :disk_path, :disk_format, :size_gb, :controller_type, :controller_number, :controller_location)
            ON CONFLICT DO NOTHING
        """),
            {
                "vm_id": d.get("VMId"),
                "cluster_name": cluster_name,
                "disk_name": d.get("DiskName"),
                "disk_path": d.get("DiskPath"),
                "disk_format": d.get("DiskFormat"),
                "size_gb": d.get("Size", 0),
                "controller_type": d.get("ControllerType"),
                "controller_number": d.get("ControllerNumber"),
                "controller_location": d.get("ControllerLocation"),
            },
        )
    session.commit()
    logger.info(f"Saved {len(disks)} VM disks")


def save_networks(networks: List[Dict], cluster_name: str = "Unknown"):
    if not networks:
        return
    session = get_db_connection()
    for n in networks:
        session.execute(
            text("""
            INSERT INTO vm_network_adapters
            (vm_id, cluster_name, adapter_name, switch_name, mac_address, ip_addresses, is_connected, vlan_id, bandwidth_setting)
            VALUES (:vm_id, :cluster_name, :adapter_name, :switch_name, :mac_address, :ip_addresses, :is_connected, :vlan_id, :bandwidth_setting)
            ON CONFLICT DO NOTHING
        """),
            {
                "vm_id": n.get("VMId"),
                "cluster_name": cluster_name,
                "adapter_name": n.get("AdapterName"),
                "switch_name": n.get("SwitchName"),
                "mac_address": n.get("MacAddress"),
                "ip_addresses": n.get("IPAddresses"),
                "is_connected": bool(n.get("IsConnected")),
                "vlan_id": n.get("VlanId", 0),
                "bandwidth_setting": n.get("BandwidthMode"),
            },
        )
    session.commit()


def save_snapshots(snapshots: List[Dict], cluster_name: str = "Unknown"):
    if not snapshots:
        return
    session = get_db_connection()
    for s in snapshots:
        session.execute(
            text("""
            INSERT INTO vm_snapshots
            (vm_id, cluster_name, snapshot_name, snapshot_type, creation_time, parent_snapshot_id)
            VALUES (:vm_id, :cluster_name, :snapshot_name, :snapshot_type, :creation_time, :parent_snapshot_id)
            ON CONFLICT DO NOTHING
        """),
            {
                "vm_id": s.get("VMId"),
                "cluster_name": cluster_name,
                "snapshot_name": s.get("SnapshotName"),
                "snapshot_type": s.get("SnapshotType"),
                "creation_time": s.get("CreationTime"),
                "parent_snapshot_id": s.get("ParentSnapshotName"),
            },
        )
    session.commit()


def save_replication(reps: List[Dict], cluster_name: str = "Unknown"):
    if not reps:
        return
    session = get_db_connection()
    for r in reps:
        session.execute(
            text("""
            INSERT INTO vm_replication
            (vm_id, cluster_name, replication_state, replication_health, replication_mode,
             primary_server, replica_server, frequency_seconds, last_replication_time)
            VALUES (:vm_id, :cluster_name, :replication_state, :replication_health, :replication_mode,
             :primary_server, :replica_server, :frequency_seconds, :last_replication_time)
            ON CONFLICT DO NOTHING
        """),
            {
                "vm_id": r.get("VMId"),
                "cluster_name": cluster_name,
                "replication_state": r.get("ReplicationState"),
                "replication_health": r.get("ReplicationHealth"),
                "replication_mode": r.get("ReplicationMode"),
                "primary_server": r.get("PrimaryServer"),
                "replica_server": r.get("ReplicaServer"),
                "frequency_seconds": r.get("FrequencySeconds"),
                "last_replication_time": r.get("LastReplicationTime"),
            },
        )
    session.commit()
