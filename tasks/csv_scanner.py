"""CSV storage scanning functionality"""

import os
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

from celery import shared_task

logger = logging.getLogger(__name__)

from .hyperv import create_winrm_session, run_powershell_long


PS_GET_CSV_INFO = """
# CSV Storage Collection Script
$ErrorActionPreference = 'SilentlyContinue'

$volumes = Get-ClusterSharedVolume
if ($volumes) {
    $volumes | ForEach-Object {
        $vol = $_
        $csvInfo = $vol | Get-ClusterSharedVolumeState

        # Get VHD information
        $vhdInfo = Get-ChildItem -Path $csvInfo.SharedVolumeInfo.Partition.VolumeName.Path -Recurse -Filter *.vhdx -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum |
            Select-Object @{Name='VHDCount'; Expression={$_.Count}}, @{Name='TotalSize'; Expression={$_.Sum / 1GB}}

        # Get oversubscription
        $vhdMaxSize = 0
        $vhdActualSize = 0
        Get-ChildItem -Path $csvInfo.SharedVolumeInfo.Partition.VolumeName.Path -Recurse -Filter *.vhdx -ErrorAction SilentlyContinue | ForEach-Object {
            $vhd = Get-VHD -Path $_.FullName -ErrorAction SilentlyContinue
            if ($vhd) {
                $vhdMaxSize += $vhd.Size / 1GB
                $vhdActualSize += $vhd.FileSize / 1GB
            }
        }

        [PSCustomObject]@{
            Name = $vol.Name
            VolumePath = $csvInfo.SharedVolumeInfo.Partition.VolumeName.Path
            OwnerNode = $csvInfo.OwnerNode.Name
            State = $vol.State.ToString()
            TotalSizeGB = [math]::Round($csvInfo.SharedVolumeInfo.Partition.Size / 1GB, 2)
            FreeSpaceGB = [math]::Round($csvInfo.SharedVolumeInfo.Partition.FreeSpace / 1GB, 2)
            UsedSpaceGB = [math]::Round(($csvInfo.SharedVolumeInfo.Partition.Size - $csvInfo.SharedVolumeInfo.Partition.FreeSpace) / 1GB, 2)
            PercentUsed = [math]::Round((($csvInfo.SharedVolumeInfo.Partition.Size - $csvInfo.SharedVolumeInfo.Partition.FreeSpace) / $csvInfo.SharedVolumeInfo.Partition.Size) * 100, 2)
            MaintenanceMode = if ($csvInfo.MaintenanceMode) { 1 } else { 0 }
            RedirectedAccess = if ($csvInfo.RedirectedAccess) { 1 } else { 0 }
            VHDCount = if ($vhdInfo) { $vhdInfo.VHDCount } else { 0 }
            VHDMaxSizeGB = [math]::Round($vhdMaxSize, 2)
            VHDActualSizeGB = [math]::Round($vhdActualSize, 2)
            OversubscriptionPercent = if ($csvInfo.SharedVolumeInfo.Partition.Size -gt 0) { [math]::Round(($vhdMaxSize / ($csvInfo.SharedVolumeInfo.Partition.Size / 1GB)) * 100, 2) } else { 0 }
            OversubscriptionGB = [math]::Round($vhdMaxSize - ($csvInfo.SharedVolumeInfo.Partition.Size / 1GB), 2)
        }
    } | ConvertTo-Json -Depth 3
} else {
    "[]" | ConvertTo-Json
}
"""


def save_csv_to_db(csv_list: List[Dict], cluster_name: str):
    """Save Cluster Shared Volume information to database with explicit cluster_name.

    Args:
        csv_list: List of CSV volume dictionaries from PowerShell
        cluster_name: Explicit cluster name (NO inference from owner_node)
    """
    if not csv_list:
        return

    from app.utils.db import get_db_connection

    conn = get_db_connection()
    cursor = conn.cursor()

    for csv in csv_list:
        owner_node = csv.get("OwnerNode", "")

        # Use explicit cluster_name parameter directly
        # No inference from owner_node or hyperv_hosts lookup

        cursor.execute(
            """
            INSERT OR REPLACE INTO cluster_shared_volumes (
                name, volume_path, owner_node, state,
                total_size_gb, free_space_gb, used_space_gb, percent_used,
                maintenance_mode, redirected_access,
                vhd_count, vhd_max_size_gb, vhd_actual_size_gb,
                oversubscription_percent, oversubscription_gb, cluster_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                csv.get("Name"),
                csv.get("VolumePath"),
                owner_node,
                csv.get("State"),
                csv.get("TotalSizeGB", 0),
                csv.get("FreeSpaceGB", 0),
                csv.get("UsedSpaceGB", 0),
                csv.get("PercentUsed", 0),
                1 if csv.get("MaintenanceMode") else 0,
                1 if csv.get("RedirectedAccess") else 0,
                csv.get("VHDCount", 0),
                csv.get("VHDMaxSizeGB", 0),
                csv.get("VHDActualSizeGB", 0),
                csv.get("OversubscriptionPercent", 0),
                csv.get("OversubscriptionGB", 0),
                cluster_name,
            ),
        )

    conn.commit()
    conn.close()
    logger.info(f"Saved {len(csv_list)} CSV records for cluster '{cluster_name}'")


def should_rescan_csv(cluster_id: int = None) -> bool:
    """Check if CSV data needs to be refreshed based on cache time.

    Args:
        cluster_id: Optional cluster ID for per-cluster cache checking.
                   If None, uses legacy global cache (id=1).

    Returns:
        True if rescan is needed, False otherwise
    """
    from app.utils.db import get_db_connection

    conn = get_db_connection()
    cursor = conn.cursor()

    if cluster_id:
        # Per-cluster cache check
        cursor.execute(
            """
            SELECT last_scan_end FROM csv_scan_metadata
            WHERE cluster_id = ?
        """,
            (cluster_id,),
        )
    else:
        # Legacy global cache check (backward compatibility)
        cursor.execute("""
            SELECT last_scan_end FROM csv_scan_metadata WHERE id = 1
        """)

    result = cursor.fetchone()
    conn.close()

    if not result or not result[0]:
        # Never scanned, should scan
        return True

    try:
        last_scan = datetime.strptime(result[0], "%Y-%m-%d %H:%M:%S")
        time_since_scan = (datetime.now() - last_scan).total_seconds()

        # Rescan if more than 1 hour (3600 seconds) has passed
        CSV_CACHE_SECONDS = int(os.getenv("CSV_CACHE_SECONDS", "3600"))
        return time_since_scan > CSV_CACHE_SECONDS
    except:
        # Error parsing date, should rescan
        return True


def update_csv_scan_metadata(
    status: str,
    cluster_id: int = None,
    volumes_scanned: int = 0,
    total_vhds: int = 0,
    duration: float = 0,
    errors: Optional[str] = None,
    start: bool = False,
):
    """Update CSV scan metadata.

    Args:
        status: Scan status (running/success/error/skipped)
        cluster_id: Optional cluster ID for per-cluster tracking
        volumes_scanned: Number of volumes scanned
        total_vhds: Total VHDs found
        duration: Scan duration in seconds
        errors: Error message if any
        start: True if starting scan, False if ending
    """
    from app.utils.db import get_db_connection

    conn = get_db_connection()
    cursor = conn.cursor()

    if cluster_id:
        # Per-cluster metadata update
        if start:
            cursor.execute(
                """
                INSERT OR REPLACE INTO csv_scan_metadata 
                (id, cluster_id, last_scan_start, scan_status)
                VALUES ((SELECT id FROM csv_scan_metadata WHERE cluster_id = ?), ?, ?, ?)
            """,
                (
                    cluster_id,
                    cluster_id,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    status,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE csv_scan_metadata
                SET last_scan_end = ?,
                    scan_status = ?,
                    volumes_scanned = ?,
                    total_vhds_found = ?,
                    scan_duration_seconds = ?,
                    errors = ?
                WHERE cluster_id = ?
            """,
                (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    status,
                    volumes_scanned,
                    total_vhds,
                    duration,
                    errors,
                    cluster_id,
                ),
            )
    else:
        # Legacy global metadata update (backward compatibility)
        if start:
            cursor.execute(
                """
                INSERT OR REPLACE INTO csv_scan_metadata (id, last_scan_start, scan_status)
                VALUES (1, ?, ?)
            """,
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status),
            )
        else:
            update_fields = [
                "last_scan_end = ?",
                "scan_status = ?",
                "volumes_scanned = ?",
                "total_vhds_found = ?",
                "scan_duration_seconds = ?",
            ]
            update_values = [
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                status,
                volumes_scanned,
                total_vhds,
                duration,
            ]

            if errors is not None:
                update_fields.append("errors = ?")
                update_values.append(errors)

            query = (
                f"UPDATE csv_scan_metadata SET {', '.join(update_fields)} WHERE id = ?"
            )
            update_values.append(1)
            cursor.execute(query, update_values)

    conn.commit()
    conn.close()
    logger.info(f"CSV scan metadata updated: {status}")


@shared_task(name="tasks.csv_scanner.fetch_cluster_csv_storage")
def fetch_cluster_csv_storage(cluster_id: int, cluster_name: str, nodes: list):
    """Scan CSV storage for a specific cluster with explicit cluster attribution.

    Args:
        cluster_id: Database cluster ID for credential lookup
        cluster_name: Explicit cluster name (passed to save_csv_to_db)
        nodes: List of cluster node hostnames

    Returns:
        Dict with status, cluster name, volumes scanned
    """
    logger.info(f"[Cluster: {cluster_name}] Starting CSV scan")

    if not nodes:
        error_msg = f"No nodes available for CSV scan in {cluster_name}"
        logger.error(error_msg)
        return {"status": "error", "cluster": cluster_name, "message": error_msg}

    # Check per-cluster cache
    if not should_rescan_csv(cluster_id):
        logger.info(f"[Cluster: {cluster_name}] CSV cache is fresh, skipping scan")
        return {
            "status": "skipped",
            "cluster": cluster_name,
            "message": "Using cached data",
        }

    # Try each cluster node until one succeeds
    for node in nodes:
        try:
            logger.info(f"[Cluster: {cluster_name}] Connecting to {node} for CSV scan")
            session = create_winrm_session(node, cluster_id=cluster_id)

            csv_scan_start = datetime.now()
            update_csv_scan_metadata("running", cluster_id=cluster_id, start=True)

            # Use Base64 wrapper for large scripts
            csv_info = run_powershell_long(session, PS_GET_CSV_INFO)

            if csv_info:
                # EXPLICIT cluster_name - NO inference
                save_csv_to_db(csv_info, cluster_name=cluster_name)

            # Calculate scan duration
            scan_duration = (datetime.now() - csv_scan_start).total_seconds()
            total_vhds = (
                sum([vol.get("VHDCount", 0) for vol in csv_info]) if csv_info else 0
            )
            vol_count = len(csv_info) if csv_info else 0

            update_csv_scan_metadata(
                "success",
                cluster_id=cluster_id,
                volumes_scanned=vol_count,
                total_vhds=total_vhds,
                duration=scan_duration,
            )

            logger.info(
                f"[Cluster: {cluster_name}] CSV scan completed in {scan_duration:.1f}s: "
                f"{vol_count} volumes, {total_vhds} VHDs"
            )

            return {
                "status": "success",
                "cluster": cluster_name,
                "volumes": vol_count,
                "vhds": total_vhds,
                "duration": scan_duration,
                "node": node,
            }

        except Exception as e:
            import winrm

            if isinstance(
                e,
                (
                    winrm.exceptions.WinRMTransportError,
                    winrm.exceptions.WinRMOperationTimeoutError,
                ),
            ):
                logger.warning(
                    f"[Cluster: {cluster_name}] Connection error on {node}: {e}"
                )
                continue
            else:
                logger.warning(
                    f"[Cluster: {cluster_name}] Unexpected error on {node}: {e}"
                )
                continue

    # All nodes failed
    error_msg = f"Failed to connect to any node in cluster {cluster_name}"
    logger.error(error_msg)
    update_csv_scan_metadata("error", cluster_id=cluster_id, errors=error_msg)
    return {"status": "error", "cluster": cluster_name, "message": error_msg}
