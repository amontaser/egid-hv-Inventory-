"""Persist host and physical disk data to SQLite."""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import text

from app.db import get_db_connection

logger = logging.getLogger(__name__)


def save_host(
    host_info: Dict,
    cluster_name: Optional[str] = None,
    connection_ip: Optional[str] = None,
):
    if not host_info:
        return
    session = get_db_connection()
    effective_cluster = (
        cluster_name if cluster_name is not None else host_info.get("ClusterName")
    )
    session.execute(
        text("""
        INSERT INTO hyperv_hosts (
            host_name, cluster_name, total_memory_gb, available_memory_gb,
            logical_processors, vm_count, os_version, hyperv_version,
            vhd_default_path, vm_default_path, connection_ip, last_updated
        ) VALUES (
            :host_name, :cluster_name, :total_memory_gb, :available_memory_gb,
            :logical_processors, :vm_count, :os_version, :hyperv_version,
            :vhd_default_path, :vm_default_path, :connection_ip, :last_updated
        )
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
    """),
        {
            "host_name": host_info.get("HostName"),
            "cluster_name": effective_cluster,
            "total_memory_gb": host_info.get("TotalMemoryGB", 0),
            "available_memory_gb": host_info.get("AvailableMemoryGB", 0),
            "logical_processors": host_info.get("LogicalProcessors", 0),
            "vm_count": host_info.get("VMCount", 0),
            "os_version": host_info.get("OSVersion"),
            "hyperv_version": host_info.get("HyperVVersion"),
            "vhd_default_path": host_info.get("VirtualHardDiskPath"),
            "vm_default_path": host_info.get("VirtualMachinePath"),
            "connection_ip": connection_ip,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    session.commit()
    logger.info(f"Saved host {host_info.get('HostName')}")


def save_physical_disks(disks: List[Dict], host_name: str):
    if not disks:
        return
    session = get_db_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    session.execute(
        text("DELETE FROM host_physical_disks WHERE host_name = :host_name"),
        {"host_name": host_name},
    )
    for d in disks:
        session.execute(
            text("""
            INSERT INTO host_physical_disks
            (host_name, friendly_name, serial_number, media_type, size_gb,
             health_status, operational_status, bus_type, partition_style, disk_number, last_updated)
            VALUES (:host_name, :friendly_name, :serial_number, :media_type, :size_gb,
             :health_status, :operational_status, :bus_type, :partition_style, :disk_number, :last_updated)
            ON CONFLICT (host_name, serial_number) DO UPDATE SET
                friendly_name       = EXCLUDED.friendly_name,
                media_type          = EXCLUDED.media_type,
                size_gb             = EXCLUDED.size_gb,
                health_status       = EXCLUDED.health_status,
                operational_status  = EXCLUDED.operational_status,
                bus_type            = EXCLUDED.bus_type,
                partition_style     = EXCLUDED.partition_style,
                disk_number         = EXCLUDED.disk_number,
                last_updated        = EXCLUDED.last_updated
        """),
            {
                "host_name": host_name,
                "friendly_name": d.get("FriendlyName"),
                "serial_number": d.get("SerialNumber"),
                "media_type": d.get("MediaType"),
                "size_gb": d.get("SizeGB", 0),
                "health_status": d.get("HealthStatus"),
                "operational_status": d.get("OperationalStatus"),
                "bus_type": d.get("BusType"),
                "partition_style": d.get("PartitionStyle"),
                "disk_number": d.get("DiskNumber"),
                "last_updated": now,
            },
        )
    session.commit()
    logger.info(f"Saved {len(disks)} physical disks for {host_name}")
