"""Persist host and physical disk data to SQLite."""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from app.db import get_db_connection

logger = logging.getLogger(__name__)


def save_host(
    host_info: Dict,
    cluster_name: Optional[str] = None,
    connection_ip: Optional[str] = None,
):
    if not host_info:
        return
    conn = get_db_connection()
    effective_cluster = (
        cluster_name if cluster_name is not None else host_info.get("ClusterName")
    )
    conn.execute(
        """
        INSERT INTO hyperv_hosts (
            host_name, cluster_name, total_memory_gb, available_memory_gb,
            logical_processors, vm_count, os_version, hyperv_version,
            vhd_default_path, vm_default_path, connection_ip, last_updated
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(host_name) DO UPDATE SET
            cluster_name        = excluded.cluster_name,
            total_memory_gb     = excluded.total_memory_gb,
            available_memory_gb = excluded.available_memory_gb,
            logical_processors  = excluded.logical_processors,
            vm_count            = excluded.vm_count,
            os_version          = excluded.os_version,
            hyperv_version      = excluded.hyperv_version,
            vhd_default_path    = excluded.vhd_default_path,
            vm_default_path     = excluded.vm_default_path,
            connection_ip       = excluded.connection_ip,
            last_updated        = excluded.last_updated
    """,
        (
            host_info.get("HostName"),
            effective_cluster,
            host_info.get("TotalMemoryGB", 0),
            host_info.get("AvailableMemoryGB", 0),
            host_info.get("LogicalProcessors", 0),
            host_info.get("VMCount", 0),
            host_info.get("OSVersion"),
            host_info.get("HyperVVersion"),
            host_info.get("VirtualHardDiskPath"),
            host_info.get("VirtualMachinePath"),
            connection_ip,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()
    logger.info(f"Saved host {host_info.get('HostName')}")


def save_physical_disks(disks: List[Dict], host_name: str):
    if not disks:
        return
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Clear old disks for this host then re-insert (disk list is authoritative)
    c.execute("DELETE FROM host_physical_disks WHERE host_name = ?", (host_name,))
    for d in disks:
        c.execute(
            """
            INSERT OR REPLACE INTO host_physical_disks
            (host_name, friendly_name, serial_number, media_type, size_gb,
             health_status, operational_status, bus_type, partition_style, disk_number, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
            (
                host_name,
                d.get("FriendlyName"),
                d.get("SerialNumber"),
                d.get("MediaType"),
                d.get("SizeGB", 0),
                d.get("HealthStatus"),
                d.get("OperationalStatus"),
                d.get("BusType"),
                d.get("PartitionStyle"),
                d.get("DiskNumber"),
                now,
            ),
        )
    conn.commit()
    conn.close()
    logger.info(f"Saved {len(disks)} physical disks for {host_name}")
