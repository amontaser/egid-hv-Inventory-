"""Main data synchronization logic"""

import os
import json
import logging
import socket
from datetime import datetime
from typing import Dict, List, Any, Optional

from celery import Celery, shared_task, group, chord

logger = logging.getLogger(__name__)

# Import from our new modules
from .hyperv import create_winrm_session, run_powershell
from .csv_scanner import fetch_cluster_csv_storage
from app.utils.db import get_db_connection


def resolve_node_ip(node_name: str, dns_servers_str: str, domain_name: str = None) -> str:
    """Resolve a cluster node name to an IP using configured DNS servers.

    Args:
        node_name: Short node name from Get-ClusterNode (e.g. 'NODE1')
        dns_servers_str: Comma-separated DNS server IPs from cluster config
        domain_name: Domain suffix to build FQDN (e.g. 'egitdr.corp')

    Returns:
        Resolved IP address, or node_name if resolution fails
    """
    import dns.resolver

    if not dns_servers_str or not dns_servers_str.strip():
        # No custom DNS — fall back to system resolver
        try:
            return socket.gethostbyname(node_name)
        except socket.gaierror:
            logger.warning(f"System DNS could not resolve {node_name}")
            return node_name

    dns_servers = [s.strip() for s in dns_servers_str.split(",") if s.strip()]
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = dns_servers
    resolver.timeout = 3
    resolver.lifetime = 5

    # Try FQDN first when domain_name is configured
    if domain_name:
        fqdn = f"{node_name}.{domain_name}"
        try:
            answer = resolver.resolve(fqdn, "A")
            ip = str(answer[0])
            logger.info(f"Resolved {fqdn} -> {ip}")
            return ip
        except Exception:
            pass  # fall through to short name

    # Try short name
    try:
        answer = resolver.resolve(node_name, "A")
        ip = str(answer[0])
        logger.info(f"Resolved {node_name} -> {ip}")
        return ip
    except Exception:
        logger.warning(f"Could not resolve {node_name} via custom DNS, using as-is")
        return node_name


# ============================================================================
# PowerShell Scripts (extracted from tasks.py lines 69-480)
# ============================================================================

PS_GET_VMS = """
$ErrorActionPreference = "SilentlyContinue"

# Get cluster name if this host is part of a cluster
$clusterName = $null
try {
    $cluster = Get-Cluster -ErrorAction SilentlyContinue
    if ($cluster) { $clusterName = $cluster.Name }
} catch {}

Get-VM | ForEach-Object {
    $vm = $_

    # Calculate uptime
    $uptime = 0
    if ($vm.State -eq 'Running' -and $vm.Uptime) {
        $uptime = [int]$vm.Uptime.TotalSeconds
    }

    [PSCustomObject]@{
        VMId = $vm.Id
        Name = $vm.Name
        State = $vm.State.ToString()
        ComputerName = $vm.ComputerName
        CPUCount = $vm.ProcessorCount
        MemoryAssigned = [math]::Round($vm.MemoryAssigned / 1GB, 2)
        MemoryDemand = [math]::Round($vm.MemoryDemand / 1GB, 2)
        MemoryStartup = [math]::Round($vm.MemoryStartup / 1GB, 2)
        MemoryMinimum = [math]::Round($vm.MemoryMinimum / 1GB, 2)
        MemoryMaximum = [math]::Round($vm.MemoryMaximum / 1GB, 2)
        DynamicMemory = $vm.DynamicMemoryEnabled
        Generation = $vm.Generation
        Version = $vm.Version
        IntegrationServicesVersion = if ($vm.IntegrationServicesVersion) { $vm.IntegrationServicesVersion.ToString() } else { $null }
        VirtualHardDiskPath = if ($vm.VirtualHardDisks.Count -gt 0) { $vm.VirtualHardDisks[0].Path } else { $null }
        VirtualMachinePath = $vm.ConfigurationLocation
        PrimaryIPAddress = ($vm.NetworkAdapters | Where-Object {$_.IPAddresses} | Select-Object -First 1 -ExpandProperty IPAddresses) -join ','
        ClusterName = $clusterName
        CreatedTime = if ($vm.CreationTime) { $vm.CreationTime.ToString("yyyy-MM-dd HH:mm:ss") } else { $null }
    } | ConvertTo-Json -Depth 3
} | ConvertTo-Json -Depth 3
"""

PS_GET_VM_DISKS = """
$ErrorActionPreference = "SilentlyContinue"
Get-VM | ForEach-Object {
    $vm = $_
    $vm.VirtualHardDisks | ForEach-Object {
        [PSCustomObject]@{
            VMId = $vm.Id
            DiskName = $_.Name
            DiskPath = $_.Path
            DiskFormat = $_.VhdFormat.ToString()
            Size = [math]::Round($_.FileSize / 1GB, 2)
            ControllerType = $_.ControllerType.ToString()
            ControllerNumber = $_.ControllerNumber
            ControllerLocation = $_.ControllerLocation
        }
    }
} | ConvertTo-Json -Depth 3
"""

PS_GET_VM_NETWORKS = """
$ErrorActionPreference = "SilentlyContinue"
Get-VM | ForEach-Object {
    $vm = $_
    $vm.NetworkAdapters | ForEach-Object {
        [PSCustomObject]@{
            VMId = $vm.Id
            AdapterName = $_.Name
            SwitchName = $_.SwitchName
            MacAddress = $_.MacAddress
            IPAddresses = $_.IPAddresses -join ','
            IsConnected = $_.Status -eq 'Ok'
            BandwidthMode = if ($vm.BandwidthMode) { $vm.BandwidthMode.GetType().Name } else { $null }
        }
    }
} | ConvertTo-Json -Depth 3
"""

PS_GET_VM_SNAPSHOTS = """
$ErrorActionPreference = "SilentlyContinue"
Get-VM | ForEach-Object {
    $vm = $_
    $vm.Snapshots | ForEach-Object {
        [PSCustomObject]@{
            VMId = $vm.Id
            SnapshotName = $_.Name
            SnapshotType = $_.SnapshotType.ToString()
            CreationTime = $_.CreationTime.ToString("yyyy-MM-dd HH:mm:ss")
            ParentSnapshotName = if ($_.ParentSnapshot) { $_.ParentSnapshot.Name } else { $null }
        }
    }
} | ConvertTo-Json -Depth 3
"""

PS_GET_VM_REPLICATION = """
$ErrorActionPreference = "SilentlyContinue"
Get-VM | ForEach-Object {
    $vm = $_
    $replication = $vm | Get-VMReplication
    if ($replication) {
        [PSCustomObject]@{
            VMId = $vm.Id
            ReplicationState = $replication.State.ToString()
            ReplicationHealth = $replication.Health.ToString()
            ReplicationMode = $replication.ReplicationMode.ToString()
            PrimaryServer = $replication.PrimaryServer
            ReplicaServer = $replication.ReplicaServer
            FrequencySeconds = if ($replication.ReplicationFrequency) { $replication.ReplicationFrequency * 60 } else { $null }
            LastReplicationTime = if ($replication.LastReplicationTime) { $replication.LastReplicationTime.ToString("yyyy-MM-dd HH:mm:ss") } else { $null }
        }
    }
} | ConvertTo-Json -Depth 3
"""

PS_GET_HOST_INFO = """
$ErrorActionPreference = "SilentlyContinue"

# Get cluster name
$clusterName = $null
try {
    $cluster = Get-Cluster -ErrorAction SilentlyContinue
    if ($cluster) { $clusterName = $cluster.Name }
} catch {}

# Get memory info
$memory = Get-WmiObject -Class Win32_OperatingSystem
$totalMemoryGB = [math]::Round($memory.TotalVisibleMemorySize / 1MB, 2)
$availableMemoryGB = [math]::Round($memory.FreePhysicalMemory / 1MB, 2)

# Get processor info
$processor = Get-WmiObject -Class Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum
$logicalProcessors = $processor.Sum

# Get VM count
$vmCount = (Get-VM).Count

# Get OS version
$os = Get-WmiObject -Class Win32_OperatingSystem
$osVersion = $os.Caption + " " + $os.Version

# Get Hyper-V version
$hypervVersion = $null
try {
    $hvHost = Get-VMHost
    $hypervVersion = $hvHost.IntegrationServicesVersion
} catch {}

[PSCustomObject]@{
    HostName = $env:COMPUTERNAME
    ClusterName = $clusterName
    TotalMemoryGB = $totalMemoryGB
    AvailableMemoryGB = $availableMemoryGB
    LogicalProcessors = $logicalProcessors
    VMCount = $vmCount
    OSVersion = $osVersion
    HyperVVersion = if ($hypervVersion) { $hypervVersion.ToString() } else { $null }
    VirtualHardDiskPath = $hvHost.VirtualHardDiskPath
    VirtualMachinePath = $hvHost.VirtualMachinePath
    EnableEnhancedSessionMode = $hvHost.EnableEnhancedSessionMode
    NumaSpanningEnabled = $hvHost.NumaSpanningEnabled
} | ConvertTo-Json -Depth 2
"""

PS_GET_CLUSTER_NODES = """
$ErrorActionPreference = "SilentlyContinue"

# Get cluster nodes
try {
    $nodes = Get-ClusterNode -ErrorAction Stop
    $nodes | ForEach-Object {
        [PSCustomObject]@{
            Name = $_.Name
            State = $_.State.ToString()
        }
    } | ConvertTo-Json -Depth 3
} catch {
    @() | ConvertTo-Json
}
"""


# ============================================================================
# Database Save Functions (extracted from tasks.py lines 887-1140)
# ============================================================================


def save_vms_to_db(vms: List[Dict], host_name: str):
    """Save VM information to database."""
    if not vms:
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    for vm in vms:
        cursor.execute(
            """
            INSERT OR REPLACE INTO vm_info (
                vm_id, machine_name, host_name, cluster_name, state,
                uptime_seconds, cpu_count, memory_assigned_gb, memory_demand_gb,
                memory_startup_gb, memory_minimum_gb, memory_maximum_gb,
                dynamic_memory_enabled, generation, version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                vm.get("VMId"),
                vm.get("Name"),
                host_name,
                vm.get("ClusterName"),
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
    logger.info(f"Saved {len(vms)} VMs for host {host_name}")


def save_disks_to_db(disks: List[Dict]):
    """Save VM disk information to database."""
    if not disks:
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    for disk in disks:
        cursor.execute(
            """
            INSERT OR REPLACE INTO vm_disks (
                vm_id, disk_name, disk_path, disk_format,
                size_gb, controller_type, controller_number, controller_location
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                disk.get("VMId"),
                disk.get("DiskName"),
                disk.get("DiskPath"),
                disk.get("DiskFormat"),
                disk.get("Size", 0),
                disk.get("ControllerType"),
                disk.get("ControllerNumber"),
                disk.get("ControllerLocation"),
            ),
        )

    conn.commit()
    conn.close()
    logger.info(f"Saved {len(disks)} VM disks")


def save_networks_to_db(networks: List[Dict]):
    """Save VM network adapter information to database."""
    if not networks:
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    for net in networks:
        cursor.execute(
            """
            INSERT OR REPLACE INTO vm_network_adapters (
                vm_id, adapter_name, switch_name, mac_address,
                ip_addresses, is_connected, bandwidth_setting
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                net.get("VMId"),
                net.get("AdapterName"),
                net.get("SwitchName"),
                net.get("MacAddress"),
                net.get("IPAddresses"),
                1 if net.get("IsConnected") else 0,
                net.get("BandwidthMode"),
            ),
        )

    conn.commit()
    conn.close()
    logger.info(f"Saved {len(networks)} network adapters")


def save_snapshots_to_db(snapshots: List[Dict]):
    """Save VM snapshot information to database."""
    if not snapshots:
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    for snap in snapshots:
        cursor.execute(
            """
            INSERT OR REPLACE INTO vm_snapshots (
                vm_id, snapshot_name, snapshot_type,
                creation_time, parent_snapshot_id
            ) VALUES (?, ?, ?, ?, ?)
        """,
            (
                snap.get("VMId"),
                snap.get("SnapshotName"),
                snap.get("SnapshotType"),
                snap.get("CreationTime"),
                snap.get("ParentSnapshotName"),
            ),
        )

    conn.commit()
    conn.close()
    logger.info(f"Saved {len(snapshots)} snapshots")


def save_replication_to_db(replications: List[Dict]):
    """Save VM replication information to database."""
    if not replications:
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    for rep in replications:
        cursor.execute(
            """
            INSERT OR REPLACE INTO vm_replication (
                vm_id, replication_state, replication_health,
                replication_mode, primary_server, replica_server,
                frequency_seconds, last_replication_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                rep.get("VMId"),
                rep.get("ReplicationState"),
                rep.get("ReplicationHealth"),
                rep.get("ReplicationMode"),
                rep.get("PrimaryServer"),
                rep.get("ReplicaServer"),
                rep.get("FrequencySeconds"),
                rep.get("LastReplicationTime"),
            ),
        )

    conn.commit()
    conn.close()
    logger.info(f"Saved {len(replications)} replication records")


def save_host_to_db(host_info: Dict):
    """Save Hyper-V host information to database."""
    if not host_info:
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO hyperv_hosts (
            host_name, cluster_name, total_memory_gb, available_memory_gb,
            logical_processors, vm_count, os_version, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            host_info.get("HostName"),
            host_info.get("ClusterName"),
            host_info.get("TotalMemoryGB", 0),
            host_info.get("AvailableMemoryGB", 0),
            host_info.get("LogicalProcessors", 0),
            host_info.get("VMCount", 0),
            host_info.get("OSVersion"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )

    conn.commit()
    conn.close()
    logger.info(f"Saved host info for {host_info.get('HostName')}")


# ============================================================================
# Helper Functions
# ============================================================================


def update_sync_metadata(
    status: str,
    vms_synced: int = 0,
    errors: Optional[str] = None,
    start: bool = False,
    hosts_discovered: Optional[int] = None,
    hosts_processed: Optional[int] = None,
    current_host: Optional[str] = None,
):
    """Update sync metadata."""
    conn = get_db_connection()
    cursor = conn.cursor()

    if start:
        cursor.execute(
            """
            INSERT OR REPLACE INTO sync_metadata (id, last_sync_start, last_sync_status, hosts_discovered, hosts_processed)
            VALUES (1, ?, ?, ?, ?)
        """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "running",
                hosts_discovered or 0,
                0,
            ),
        )
    else:
        update_fields = ["last_sync_end = ?", "last_sync_status = ?", "vms_synced = ?"]
        update_values = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status,
            vms_synced,
        ]

        if hosts_discovered is not None:
            update_fields.append("hosts_discovered = ?")
            update_values.append(hosts_discovered)

        if hosts_processed is not None:
            update_fields.append("hosts_processed = ?")
            update_values.append(hosts_processed)

        if current_host is not None:
            update_fields.append("current_host = ?")
            update_values.append(current_host)

        if errors is not None:
            update_fields.append("errors = ?")
            update_values.append(errors)

        query = f"UPDATE sync_metadata SET {', '.join(update_fields)} WHERE id = ?"
        update_values.append(1)
        cursor.execute(query, update_values)

    conn.commit()
    conn.close()
    logger.info(f"Sync metadata updated: {status}")


def clear_old_data():
    """Clear existing VM data before fresh sync."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM vm_info")
    cursor.execute("DELETE FROM vm_disks")
    cursor.execute("DELETE FROM vm_network_adapters")
    cursor.execute("DELETE FROM vm_snapshots")
    cursor.execute("DELETE FROM vm_replication")

    conn.commit()
    conn.close()
    logger.info("Cleared old VM data")


def discover_cluster_nodes(cluster_name: str, cluster_id: int = None) -> Dict[str, str]:
    """Discover all Up nodes in a Hyper-V cluster and resolve their IPs.

    Connects directly to the cluster FQDN (domain field) to run Get-ClusterNode,
    then resolves each node name to an IP using the cluster's configured DNS servers.

    Returns:
        Dict mapping node_name -> ip_address
    """
    logger.info(f"Discovering nodes for cluster: {cluster_name}")

    domain = cluster_name  # fallback if no DB record
    dns_servers = None
    domain_name = None

    if cluster_id:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT domain, dns_servers, domain_name FROM clusters WHERE id = ?",
                (cluster_id,),
            )
            result = cursor.fetchone()
            conn.close()
            if result:
                domain = result[0] or cluster_name
                dns_servers = result[1]
                domain_name = result[2]
                logger.info(
                    f"Cluster config: domain={domain}, dns_servers={dns_servers}, "
                    f"domain_name={domain_name}"
                )
        except Exception as e:
            logger.warning(f"Failed to get cluster config from DB: {e}")

    # Connect directly to the cluster FQDN
    logger.info(f"Connecting to cluster FQDN: {domain}")
    session = create_winrm_session(domain, cluster_id=cluster_id)

    # Query cluster nodes
    nodes_info = run_powershell(session, PS_GET_CLUSTER_NODES)
    if not nodes_info:
        raise Exception(f"Get-ClusterNode returned no results from {domain}")

    node_map = {}
    for node in nodes_info:
        state = node.get("State", "")
        node_name = node.get("Name", "")
        if state != "Up":
            logger.info(f"Skipping node {node_name} — state={state}")
            continue
        ip = resolve_node_ip(node_name, dns_servers, domain_name)
        node_map[node_name] = ip
        logger.info(f"Node {node_name} -> {ip}")

    if not node_map:
        raise Exception(f"No Up nodes found in cluster {cluster_name}")

    logger.info(f"Discovered {len(node_map)} node(s): {list(node_map.keys())}")
    return node_map


def aggregate_sync_results(results):
    """Aggregate results from parallel worker tasks."""
    logger.info(f"Aggregating results from {len(results)} worker tasks")

    total_vms = 0
    errors = []

    # Process results from all workers
    for result in results:
        if isinstance(result, dict):
            if result.get("status") == "success":
                total_vms += result.get("vms", 0)
            elif result.get("status") == "error":
                errors.append(result.get("error", "Unknown error"))

    # Get total hosts processed from database
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT host_name) FROM vm_info")
    hosts_processed = cursor.fetchone()[0]
    conn.close()

    # Determine overall status
    status = "success" if not errors else "partial"
    error_text = "; ".join(errors) if errors else None

    # Update final sync metadata
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE sync_metadata
        SET last_sync_end = ?,
            last_sync_status = ?,
            vms_synced = ?,
            hosts_processed = ?,
            current_host = NULL,
            errors = ?
        WHERE id = 1
    """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status,
            total_vms,
            hosts_processed,
            error_text,
        ),
    )
    conn.commit()
    conn.close()

    logger.info(
        f"Completed parallel sync: {total_vms} VMs from {hosts_processed} hosts"
    )

    return {
        "status": status,
        "vms_synced": total_vms,
        "hosts_processed": hosts_processed,
        "errors": errors if errors else None,
    }


def aggregate_sync_results_with_csv(results):
    """Aggregate results from VM sync and per-cluster CSV scans."""
    logger.info(f"Aggregating results from {len(results)} tasks (VMs + CSV)")

    csv_results = {}
    total_vms = 0
    errors = []

    # Process results
    for result in results:
        if isinstance(result, dict):
            if result.get("status") == "success":
                # Check if it's a CSV result (has 'cluster' key)
                if "cluster" in result and "volumes" in result:
                    cluster = result["cluster"]
                    csv_results[cluster] = result
                    logger.info(
                        f"Cluster {cluster}: {result['volumes']} volumes, {result.get('vhds', 0)} VHDs"
                    )
                else:
                    # VM sync result
                    total_vms += result.get("vms", 0)
            elif result.get("status") == "error":
                errors.append(result.get("error", "Unknown error"))

    # Get total hosts processed from database
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT host_name) FROM vm_info")
    hosts_processed = cursor.fetchone()[0]
    conn.close()

    # Determine overall status
    status = "success" if not errors else "partial"
    error_text = "; ".join(errors) if errors else None

    # Update final sync metadata
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE sync_metadata
        SET last_sync_end = ?,
            last_sync_status = ?,
            vms_synced = ?,
            hosts_processed = ?,
            current_host = NULL,
            errors = ?
        WHERE id = 1
    """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status,
            total_vms,
            hosts_processed,
            error_text,
        ),
    )
    conn.commit()
    conn.close()

    csv_summary = f", {len(csv_results)} clusters with CSV" if csv_results else ""

    logger.info(
        f"Completed sync: {total_vms} VMs from {hosts_processed} hosts{csv_summary}"
    )

    return {
        "status": status,
        "vms_synced": total_vms,
        "hosts_processed": hosts_processed,
        "csv_results": csv_results if csv_results else None,
        "errors": errors if errors else None,
    }


# ============================================================================
# Celery Tasks
# ============================================================================


@shared_task(bind=True, name="tasks.sync.fetch_hyperv_data")
def fetch_hyperv_data(self):
    """Main task to fetch all Hyper-V data from all configured clusters using parallel workers."""
    logger.info("Starting Hyper-V data collection (parallel mode)")

    # Node discovery logic
    nodes = []
    node_to_cluster_map = {}
    cluster_info = []
    cluster_name = os.getenv("HYPERV_CLUSTER")

    # Priority 1: Auto-discover from cluster (if env var set)
    if cluster_name:
        try:
            nodes = list(discover_cluster_nodes(cluster_name).keys())
            cluster_info = [(None, cluster_name, nodes)]
        except Exception as e:
            logger.warning(f"Cluster discovery failed: {e}")

    # Priority 2: Database clusters (if env var NOT set)
    elif not cluster_name:
        conn = get_db_connection()
        cursor = conn.cursor()
        clusters = cursor.execute(
            "SELECT id, cluster_name, domain, cluster_name_for_ps FROM clusters WHERE is_enabled = 1"
        ).fetchall()
        conn.close()

        if clusters:
            logger.info(
                f"Found {len(clusters)} enabled cluster(s) in database - will sync each"
            )

            for cluster in clusters:
                cluster_id = cluster[0]
                cluster_name = cluster[1]
                domain = cluster[2] or cluster_name
                logger.info(f"Discovered cluster: {cluster_name} (domain: {domain})")

                try:
                    cluster_nodes = list(
                        discover_cluster_nodes(domain, cluster_id=cluster_id).keys()
                    )
                    cluster_info.append((cluster_id, cluster_name, cluster_nodes))
                    nodes.extend(cluster_nodes)
                    for node in cluster_nodes:
                        node_to_cluster_map[node] = cluster_id
                except Exception as e:
                    logger.error(f"Failed to discover nodes for {cluster_name}: {e}")

    # Priority 3: Manual node list (fallback)
    if not nodes:
        nodes = os.getenv("HYPERV_NODES", "").split(",")
        nodes = [n.strip() for n in nodes if n.strip()]

    if not nodes:
        error_msg = (
            "No Hyper-V nodes configured. "
            "Set HYPERV_CLUSTER for auto-discovery or HYPERV_NODES for manual configuration."
        )
        logger.error(error_msg)
        update_sync_metadata("error", errors=error_msg, hosts_discovered=0)
        return {"status": "error", "message": error_msg}

    # Initialize sync with discovered nodes count
    update_sync_metadata("running", start=True, hosts_discovered=len(nodes))

    # Clear old data before sync
    clear_old_data()

    # Create per-cluster CSV tasks
    csv_tasks = []
    if cluster_info:
        for cluster_id, cluster_name, cluster_nodes in cluster_info:
            try:
                csv_tasks.append(
                    fetch_cluster_csv_storage.s(cluster_id, cluster_name, cluster_nodes)
                )
            except Exception as e:
                logger.error(f"Failed to create CSV task for {cluster_name}: {e}")

    # Create VM sync tasks (per-host)
    vm_sync_job = group(
        fetch_single_host.s(node, node_to_cluster_map.get(node)) for node in nodes
    )

    # CSV scan tasks (per-cluster)
    csv_scan_job = group(csv_tasks) if csv_tasks else group()

    # Combine VM sync and CSV scan into single parallel job
    combined_job = group(vm_sync_job, csv_scan_job)

    # Use chord to execute in parallel and collect results
    header = combined_job
    callback = aggregate_sync_results_with_csv.s()

    # Execute asynchronously
    try:
        result = chord(header)(callback)
        logger.info("Parallel sync started asynchronously")
        return {"status": "started", "message": "Data collection started in background"}
    except Exception as e:
        logger.error(f"Parallel sync failed: {e}")
        logger.info("Falling back to sequential execution")
        # Fallback would go here
        return {"status": "error", "message": str(e)}


@shared_task(name="tasks.sync.fetch_single_host")
def fetch_single_host(host: str, cluster_id: int = None):
    """Fetch data from a single Hyper-V host (parallel worker task)."""
    logger.info(f"[Worker] Fetching data from {host}")

    # Get cluster_id for this host if not provided
    if cluster_id is None:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT c.id
                FROM clusters c
                INNER JOIN hyperv_hosts h ON h.cluster_name = c.cluster_name
                WHERE h.host_name = ?
            """,
                (host,),
            )
            result = cursor.fetchone()
            conn.close()
            if result and result[0]:
                cluster_id = result[0]
        except Exception as e:
            logger.warning(f"Failed to get cluster_id for {host}: {e}")

    try:
        session = create_winrm_session(host, cluster_id=cluster_id)

        # Fetch VMs
        vms = run_powershell(session, PS_GET_VMS)
        if vms:
            save_vms_to_db(vms, host)

        # Fetch disks
        disks = run_powershell(session, PS_GET_VM_DISKS)
        if disks:
            save_disks_to_db(disks)

        # Fetch networks
        networks = run_powershell(session, PS_GET_VM_NETWORKS)
        if networks:
            save_networks_to_db(networks)

        # Fetch snapshots
        snapshots = run_powershell(session, PS_GET_VM_SNAPSHOTS)
        if snapshots:
            save_snapshots_to_db(snapshots)

        # Fetch replication info
        replications = run_powershell(session, PS_GET_VM_REPLICATION)
        if replications:
            save_replication_to_db(replications)

        # Fetch host info
        host_info = run_powershell(session, PS_GET_HOST_INFO)
        if host_info and len(host_info) > 0:
            save_host_to_db(host_info[0])

        logger.info(f"[Worker] Completed data collection from {host}")
        return {"status": "success", "host": host, "vms": len(vms) if vms else 0}

    except Exception as e:
        logger.error(f"[Worker] Error fetching from {host}: {e}")
        return {"status": "error", "host": host, "error": str(e)}
