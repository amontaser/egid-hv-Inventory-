"""PowerShell scripts and collector functions for VMs."""

import logging
from typing import List, Dict, Optional
import winrm

from .winrm import run_ps_long

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PowerShell scripts
# ---------------------------------------------------------------------------

PS_GET_VMS = """
$ErrorActionPreference = "SilentlyContinue"
$clusterName = $null
try { $c = Get-Cluster -ErrorAction SilentlyContinue; if ($c) { $clusterName = $c.Name } } catch {}

Get-VM | ForEach-Object {
    $vm = $_
    $uptime = 0
    if ($vm.State -eq 'Running' -and $vm.Uptime) { $uptime = [int]$vm.Uptime.TotalSeconds }
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
        PrimaryIPAddress = ($vm.NetworkAdapters | Where-Object {$_.IPAddresses} | ForEach-Object {$_.IPAddresses} | Where-Object {$_ -match '^\\d+\\.\\d+\\.\\d+\\.\\d+$'} | Select-Object -First 1)
        ClusterName = $clusterName
        CreatedTime = if ($vm.CreationTime) { $vm.CreationTime.ToString("yyyy-MM-dd HH:mm:ss") } else { $null }
        UptimeSeconds = $uptime
    }
} | ConvertTo-Json -Depth 3 -Compress
"""

PS_GET_VM_DISKS = """
$ErrorActionPreference = "SilentlyContinue"
Get-VM | ForEach-Object {
    $vm = $_
    Get-VMHardDiskDrive -VM $vm -ErrorAction SilentlyContinue | ForEach-Object {
        $drive = $_
        $vhd = $null
        if ($drive.Path) { try { $vhd = Get-VHD -Path $drive.Path -ErrorAction SilentlyContinue } catch {} }
        $fmt = if ($vhd) { $vhd.VhdFormat.ToString() } elseif ($drive.Path -match '\\.vhdx$') { 'VHDX' } elseif ($drive.Path -match '\\.vhd$') { 'VHD' } else { 'Unknown' }
        [PSCustomObject]@{
            VMId = $vm.Id
            DiskName = $drive.Name
            DiskPath = $drive.Path
            DiskFormat = $fmt
            Size = if ($vhd) { [math]::Round($vhd.FileSize / 1GB, 2) } else { 0 }
            ControllerType = $drive.ControllerType.ToString()
            ControllerNumber = $drive.ControllerNumber
            ControllerLocation = $drive.ControllerLocation
        }
    }
} | ConvertTo-Json -Depth 3
"""

PS_GET_VM_NETWORKS = """
$ErrorActionPreference = "SilentlyContinue"
Get-VM | ForEach-Object {
    $vm = $_
    $vm.NetworkAdapters | ForEach-Object {
        $a = $_
        $vlan = $null
        try { $vlan = Get-VMNetworkAdapterVlan -VMNetworkAdapter $a -ErrorAction SilentlyContinue } catch {}
        $vlanId = if ($vlan -and $vlan.OperationMode -eq 'Access') { $vlan.AccessVlanId } else { 0 }
        [PSCustomObject]@{
            VMId = $vm.Id
            AdapterName = $a.Name
            SwitchName = $a.SwitchName
            MacAddress = $a.MacAddress
            IPAddresses = ($a.IPAddresses | Where-Object { $_ -match '^\\d+\\.\\d+\\.\\d+\\.\\d+$' }) -join ','
            IsConnected = $a.Status -eq 'Ok'
            VlanId = $vlanId
        }
    }
} | ConvertTo-Json -Depth 3
"""

PS_GET_VM_SNAPSHOTS = """
$ErrorActionPreference = "SilentlyContinue"
Get-VM | ForEach-Object {
    $vm = $_
    Get-VMSnapshot -VMName $vm.Name -ErrorAction SilentlyContinue | ForEach-Object {
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
    $rep = $null
    try { $rep = $vm | Get-VMReplication -ErrorAction Stop } catch {}
    if ($rep) {
        [PSCustomObject]@{
            VMId = $vm.Id
            ReplicationState = $rep.State.ToString()
            ReplicationHealth = $rep.Health.ToString()
            ReplicationMode = $rep.ReplicationMode.ToString()
            PrimaryServer = $rep.PrimaryServer
            ReplicaServer = $rep.ReplicaServer
            FrequencySeconds = if ($rep.ReplicationFrequency) { $rep.ReplicationFrequency * 60 } else { $null }
            LastReplicationTime = if ($rep.LastReplicationTime) { $rep.LastReplicationTime.ToString("yyyy-MM-dd HH:mm:ss") } else { $null }
        }
    }
} | ConvertTo-Json -Depth 3
"""


# ---------------------------------------------------------------------------
# Collector functions
# ---------------------------------------------------------------------------


def collect_vms(session: winrm.Session) -> List[Dict]:
    result = run_ps_long(session, PS_GET_VMS, context="collect_vms", force_encoded=True)
    if result:
        hosts = set(v.get("ComputerName", "?") for v in result)
        logger.info(
            f"collect_vms got {len(result)} VMs from PowerShell, hosts: {hosts}"
        )
    return result or []


def collect_disks(session: winrm.Session) -> List[Dict]:
    result = run_ps_long(
        session, PS_GET_VM_DISKS, context="collect_disks", force_encoded=True
    )
    logger.info(f"Collected {len(result or [])} VM disks")
    return result or []


def collect_networks(session: winrm.Session) -> List[Dict]:
    result = run_ps_long(
        session, PS_GET_VM_NETWORKS, context="collect_networks", force_encoded=True
    )
    return result or []


def collect_snapshots(session: winrm.Session) -> List[Dict]:
    result = run_ps_long(
        session, PS_GET_VM_SNAPSHOTS, context="collect_snapshots", force_encoded=True
    )
    return result or []


def collect_replication(session: winrm.Session) -> List[Dict]:
    result = run_ps_long(
        session,
        PS_GET_VM_REPLICATION,
        context="collect_replication",
        force_encoded=True,
    )
    return result or []
